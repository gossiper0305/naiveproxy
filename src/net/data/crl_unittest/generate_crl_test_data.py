#!/usr/bin/python
# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""This script is called without any arguments to re-generate all of the *.pem
files in the script's directory.

The https://github.com/google/der-ascii tools must be in the PATH.

These tests assume that the verification time will be 2017-03-09 00:00:00 GMT
and verified with a max CRL age of 7 days.
"""

import datetime
import subprocess
import os

from OpenSSL import crypto

import base64


HEADER = "Generated by %s. Do not edit." % os.path.split(__file__)[1]

NEXT_SERIAL = 0

# 2017-01-01 00:00 GMT
CERT_DATE = datetime.datetime(2017, 1, 1, 0, 0)

# 2018-01-01 00:00 GMT
CERT_EXPIRE = CERT_DATE + datetime.timedelta(days=365)


def DictUnion(a, b):
  return dict(a.items() + b.items())


def Der2Ascii(txt):
  p = subprocess.Popen(['der2ascii'],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE)
  stdout_data, stderr_data = p.communicate(txt)
  if p.returncode:
    raise RuntimeError('der2ascii returned %i: %s' % (p.returncode,
                                                      stderr_data))
  return stdout_data


def Ascii2Der(txt):
  p = subprocess.Popen(['ascii2der'],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE)
  stdout_data, stderr_data = p.communicate(txt)
  if p.returncode:
    raise RuntimeError('ascii2der returned %i: %s' % (p.returncode,
                                                      stderr_data))
  return stdout_data


def Ascii2OpensslDer(txt):
  der = Ascii2Der(txt)
  return 'DER:' + ''.join(['%02X' % ord(b) for b in der])


def CreateCert(name, signer, pkey=None, crl_dp=None, key_usage=None,
               is_ca=True, version=2):
  global NEXT_SERIAL
  if pkey is None:
    pkey = crypto.PKey()
    pkey.generate_key(crypto.TYPE_RSA, 1024)
  cert = crypto.X509()
  cert.set_version(version)
  cert.get_subject().CN = name
  cert.set_pubkey(pkey)
  cert.set_serial_number(NEXT_SERIAL)
  NEXT_SERIAL += 1
  cert.set_notBefore(CERT_DATE.strftime('%Y%m%d%H%M%SZ'))
  cert.set_notAfter(CERT_EXPIRE.strftime('%Y%m%d%H%M%SZ'))
  if version == 2:
    if crl_dp:
      cert.add_extensions(
          [crypto.X509Extension('crlDistributionPoints', False, crl_dp)])
    if key_usage:
      cert.add_extensions(
          [crypto.X509Extension('keyUsage', False, key_usage)])
    if is_ca is not None:
      cert.add_extensions(
          [crypto.X509Extension('basicConstraints', True,
                                'CA:%s' % ('TRUE' if is_ca else 'FALSE'))])
  if signer:
    cert.set_issuer(signer['cert'].get_subject())
    cert.sign(signer['pkey'], 'sha256')
  else:
    cert.set_issuer(cert.get_subject())
    cert.sign(pkey, 'sha256')

  result = dict(cert=cert, pkey=pkey)
  if not signer:
    signer = result
  result['signer'] = signer
  return result


ROOT_CA = CreateCert('Test CA', None)

# Multiple versions of the intermediate. All use the same name and private key.
CA = CreateCert('Test Intermediate CA', ROOT_CA,
                key_usage='critical, keyCertSign, cRLSign')
CA_NO_KEYUSAGE = CreateCert('Test Intermediate CA', ROOT_CA,
                            pkey=CA['pkey'], key_usage=None)
CA_KEYUSAGE_NOCRLSIGN = CreateCert('Test Intermediate CA', ROOT_CA,
                                   pkey=CA['pkey'],
                                   key_usage='critical, keyCertSign')

# A different CA with a different name and key.
OTHER_CA = CreateCert('Test Other Intermediate CA', ROOT_CA)

# The target cert, with a simple crlDistributionPoints pointing to an arbitrary
# URL, other crlDistributionPoints fields not set.
LEAF = CreateCert('Test Cert', CA, crl_dp='URI:http://example.com/foo.crl', is_ca=False)

# The target cert, with no basicConstraints.
LEAF_NO_BASIC_CONSTRAINTS = CreateCert('Test Cert', CA, crl_dp='URI:http://example.com/foo.crl', is_ca=None)

# The target cert, no crlDistributionPoints.
LEAF_NO_CRLDP = CreateCert('Test Cert', CA, is_ca=False)

# V1 target cert
LEAF_V1 = CreateCert('Test Cert', CA, version=0, is_ca=None)

# The target cert, crlDistributionPoints with crlIssuer and
# crlDistributionPoints set.
LEAF_CRLDP_CRLISSUER = CreateCert('Test Cert', CA, is_ca=False,
    # It doesn't seem like you can set crlIssuers through the one-line openssl
    # interface, so just do it manually.
    crl_dp=Ascii2OpensslDer('''
         SEQUENCE {
           SEQUENCE {
             [0] {
               [0] {
                 [6 PRIMITIVE] { "http://example.com/foo.crl" }
               }
             }
             [2] {
               [4] {
                 SEQUENCE {
                   SET {
                     SEQUENCE {
                       # commonName
                       OBJECT_IDENTIFIER { 2.5.4.3 }
                       UTF8String { "Test CRL Issuer CA" }
                     }
                   }
                 }
               }
             }
           }
         }
         '''))

# Self-issued intermediate with a new key signed by the |CA| key.
CA_NEW_BY_OLD = CreateCert('Test Intermediate CA', CA,
                           key_usage='critical, keyCertSign, cRLSign',
                           crl_dp='URI:http://example.com/foo.crl')

# Target cert signed by |CA_NEW_BY_OLD|'s key.
LEAF_BY_NEW = CreateCert(
    'Test Cert', CA_NEW_BY_OLD, crl_dp='URI:http://example.com/foo.crl')


def SignAsciiCRL(tbs_inner_txt, signer=CA):
  tbs_txt = 'SEQUENCE {\n%s\n}' % tbs_inner_txt
  tbs_der = Ascii2Der(tbs_txt)
  signature = crypto.sign(signer['pkey'], tbs_der, 'sha256')
  crl_text = '''
SEQUENCE {
  %s
  SEQUENCE {
    # sha256WithRSAEncryption
    OBJECT_IDENTIFIER { 1.2.840.113549.1.1.11 }
    NULL {}
  }
  BIT_STRING { `00%s` }
}
''' % (tbs_txt, signature.encode('hex'))
  CRL = Ascii2Der(crl_text)

  return CRL


def MakePemBlock(der, name):
  text = Der2Ascii(der).rstrip('\n')
  b64 = base64.b64encode(der)
  wrapped = '\n'.join(b64[pos:pos + 64] for pos in xrange(0, len(b64), 64))
  return '%s\n-----BEGIN %s-----\n%s\n-----END %s-----' % (
      text, name, wrapped, name)


def WriteStringToFile(data, path):
  with open(path, "w") as f:
    f.write(data)


def Store(fname, description, leaf, ca, crl_der, ca2=None):
  ca_cert_der = crypto.dump_certificate(crypto.FILETYPE_ASN1, ca['cert'])
  cert_der = crypto.dump_certificate(crypto.FILETYPE_ASN1, leaf['cert'])

  out = '\n\n'.join([
      HEADER,
      description,
      MakePemBlock(crl_der, 'CRL'),
      MakePemBlock(ca_cert_der, 'CA CERTIFICATE'),
      MakePemBlock(cert_der, 'CERTIFICATE')])

  if ca2:
    ca_cert_2_der = crypto.dump_certificate(crypto.FILETYPE_ASN1, ca2['cert'])
    out += '\n\n' + MakePemBlock(ca_cert_2_der, 'CA CERTIFICATE 2')

  open('%s.pem' % fname, 'w').write(out)


crl_strings = {
  'sha256WithRSAEncryption': '''
    SEQUENCE {
      OBJECT_IDENTIFIER { 1.2.840.113549.1.1.11 }
      NULL {}
    }
  ''',

  'sha384WithRSAEncryption': '''
    SEQUENCE {
      OBJECT_IDENTIFIER { 1.2.840.113549.1.1.12 }
      NULL {}
    }
  ''',

 'CA_name': '''
    SEQUENCE {
      SET {
        SEQUENCE {
          # commonName
          OBJECT_IDENTIFIER { 2.5.4.3 }
          UTF8String { "Test Intermediate CA" }
        }
      }
    }
  ''',

  'thisUpdate': 'UTCTime { "170302001122Z" }',
  'nextUpdate': 'UTCTime { "170602001122Z" }',
  'thisUpdateGeneralized': 'GeneralizedTime { "20170302001122Z" }',
  'nextUpdateGeneralized': 'GeneralizedTime { "20170602001122Z" }',
  'thisUpdate_too_old': 'UTCTime { "170301001122Z" }',
  'thisUpdate_in_future': 'UTCTime { "170310001122Z" }',
  'nextUpdate_too_old': 'UTCTime { "170308001122Z" }',

  'leaf_revoked': '''
    SEQUENCE {
      SEQUENCE {
        INTEGER { %i }
        UTCTime { "170201001122Z" }
        # no crlEntryExtensions
      }
      SEQUENCE {
        INTEGER { %i }
        UTCTime { "170201001122Z" }
        # no crlEntryExtensions
      }
      SEQUENCE {
        INTEGER { %i }
        UTCTime { "170201001122Z" }
        # no crlEntryExtensions
      }
    }
  ''' % (LEAF['cert'].get_serial_number() + 100,
         LEAF['cert'].get_serial_number(),
         LEAF['cert'].get_serial_number() + 101),

  'leaf_revoked_fake_extension': '''
    SEQUENCE {
      SEQUENCE {
        INTEGER { %i }
        UTCTime { "170201001122Z" }
        # no crlEntryExtensions
      }
      SEQUENCE {
        INTEGER { %i }
        UTCTime { "170201001122Z" }
        SEQUENCE {
          SEQUENCE {
            OBJECT_IDENTIFIER { 1.2.3.4 }
            OCTET_STRING { `5678` }
          }
        }
      }
      SEQUENCE {
        INTEGER { %i }
        UTCTime { "170201001122Z" }
        # no crlEntryExtensions
      }
    }
  ''' % (LEAF['cert'].get_serial_number() + 100,
         LEAF['cert'].get_serial_number(),
         LEAF['cert'].get_serial_number() + 101),

  'leaf_revoked_before_fake_critical_extension': '''
    SEQUENCE {
      SEQUENCE {
        INTEGER { %i }
        UTCTime { "170201001122Z" }
        # leaf revocation entry has no crlEntryExtensions
      }
      SEQUENCE {
        INTEGER { %i }
        UTCTime { "170201001122Z" }
        # next revocation entry has a critical crlEntryExtension
        SEQUENCE {
          SEQUENCE {
            OBJECT_IDENTIFIER { 1.2.3.4 }
            BOOLEAN { `ff` }
            OCTET_STRING { `5678` }
          }
        }
      }
    }
  ''' % (LEAF['cert'].get_serial_number(),
         LEAF['cert'].get_serial_number() + 101),

  'leaf_revoked_generalizedtime': '''
    SEQUENCE {
      SEQUENCE {
        INTEGER { %i }
        GeneralizedTime { "20170201001122Z" }
        # no crlEntryExtensions
      }
      SEQUENCE {
        INTEGER { %i }
        GeneralizedTime { "20170201001122Z" }
        # no crlEntryExtensions
      }
      SEQUENCE {
        INTEGER { %i }
        GeneralizedTime { "20170201001122Z" }
        # no crlEntryExtensions
      }
    }
  ''' % (LEAF['cert'].get_serial_number() + 100,
         LEAF['cert'].get_serial_number(),
         LEAF['cert'].get_serial_number() + 101),

  'fake_extension': '''
     SEQUENCE {
       OBJECT_IDENTIFIER { 1.2.3.4 }
       OCTET_STRING { `5678` }
     }
  ''',

  'fake_critical_extension': '''
     SEQUENCE {
       OBJECT_IDENTIFIER { 1.2.3.4 }
       BOOLEAN { `ff` }
       OCTET_STRING { `5678` }
     }
  ''',

  # An issuingDistributionPoint with multiple fullName values, one of which
  # matches the URI in |LEAF|'s crlDistributionPoints extension.
  'issuingDistributionPoint': '''
     SEQUENCE {
       OBJECT_IDENTIFIER { 2.5.29.28 }
       BOOLEAN { `ff` }
       OCTET_STRING {
         SEQUENCE {
           [0] {
             [0] {
               [1 PRIMITIVE] { "foo@example.com" }
               [6 PRIMITIVE] { "http://zexample.com/foo.crl" }
               [6 PRIMITIVE] { "http://example.com/foo.crl" }
               [6 PRIMITIVE] { "http://aexample.com/foo.crl" }
             }
           }
         }
       }
     }
  ''',

  'issuingDistributionPoint_wrong_uri': '''
     SEQUENCE {
       OBJECT_IDENTIFIER { 2.5.29.28 }
       BOOLEAN { `ff` }
       OCTET_STRING {
         SEQUENCE {
           [0] {
             [0] {
               [6 PRIMITIVE] { "http://example.com/FOO.CRL" }
             }
           }
         }
       }
     }
  ''',

  'issuingDistributionPoint_with_indirectCRL': '''
     SEQUENCE {
       OBJECT_IDENTIFIER { 2.5.29.28 }
       BOOLEAN { `ff` }
       OCTET_STRING {
         SEQUENCE {
           [0] {
             [0] {
               [6 PRIMITIVE] { "http://example.com/foo.crl" }
             }
           }
           [4 PRIMITIVE] { `ff` }
         }
       }
     }
  ''',

  'issuingDistributionPoint_with_onlyContainsUserCerts': '''
     SEQUENCE {
       OBJECT_IDENTIFIER { 2.5.29.28 }
       BOOLEAN { `ff` }
       OCTET_STRING {
         SEQUENCE {
           [1 PRIMITIVE] { `ff` }
         }
       }
     }
  ''',

  'issuingDistributionPoint_with_uri_and_onlyContainsUserCerts': '''
     SEQUENCE {
       OBJECT_IDENTIFIER { 2.5.29.28 }
       BOOLEAN { `ff` }
       OCTET_STRING {
         SEQUENCE {
           [0] {
             [0] {
               [6 PRIMITIVE] { "http://example.com/foo.crl" }
             }
           }
           [1 PRIMITIVE] { `ff` }
         }
       }
     }
  ''',

  'issuingDistributionPoint_with_uri_and_onlyContainsCACerts': '''
     SEQUENCE {
       OBJECT_IDENTIFIER { 2.5.29.28 }
       BOOLEAN { `ff` }
       OCTET_STRING {
         SEQUENCE {
           [0] {
             [0] {
               [6 PRIMITIVE] { "http://example.com/foo.crl" }
             }
           }
           [2 PRIMITIVE] { `ff` }
         }
       }
     }
  ''',

  'issuingDistributionPoint_with_onlyContainsCACerts': '''
     SEQUENCE {
       OBJECT_IDENTIFIER { 2.5.29.28 }
       BOOLEAN { `ff` }
       OCTET_STRING {
         SEQUENCE {
           [2 PRIMITIVE] { `ff` }
         }
       }
     }
  ''',
}


Store(
    'good',
    'Leaf covered by CRLs and not revoked',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'good_issuer_name_normalization',
    'Good, non-revoked, but issuer name in CRL requires case folding',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  SEQUENCE {
    SET {
      SEQUENCE {
        # commonName
        OBJECT_IDENTIFIER { 2.5.4.3 }
        # Name that requires case folding and type conversion.
        PrintableString { "tEST iNTERMEDIATE ca" }
      }
    }
  }
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'good_issuer_no_keyusage',
    'Leaf covered by CRLs and not revoked, issuer has no keyUsage extension',
    LEAF, CA_NO_KEYUSAGE,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings, signer=CA_NO_KEYUSAGE))


Store(
    'good_no_nextupdate',
    'Leaf covered by CRLs and not revoked, optional nextUpdate field is absent',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  # no nextUpdate
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'good_fake_extension',
    'Leaf covered by CRLs and not revoked, CRL has an irrelevant non-critical '
    'extension',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(fake_extension)s
    }
  }
''' % crl_strings))


Store(
    'good_fake_extension_no_nextupdate',
    'Leaf covered by CRLs and not revoked, CRL has an irrelevant non-critical '
    'extension',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  # no nextUpdate
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(fake_extension)s
    }
  }
''' % crl_strings))


Store(
    'good_generalizedtime',
    'Leaf covered by CRLs and not revoked, dates encoded as GeneralizedTime',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdateGeneralized)s
  %(nextUpdateGeneralized)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'good_no_version',
    'Leaf covered by CRLs and not revoked, CRL is V1',
    LEAF, CA,
    SignAsciiCRL('''
  # no version
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'good_idp_contains_uri',
    'Leaf covered by CRLs and not revoked, CRL has IDP with URI matching '
    'cert DP',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(issuingDistributionPoint)s
    }
  }
''' % crl_strings))


Store(
    'good_idp_onlycontainsusercerts',
    'Leaf covered by CRLs and not revoked, CRL has IDP with '
    'onlyContainsUserCerts',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(issuingDistributionPoint_with_onlyContainsUserCerts)s
    }
  }
''' % crl_strings))


Store(
    'good_idp_onlycontainsusercerts_no_basic_constraints',
    'Leaf covered by CRLs and not revoked, CRL has IDP with '
    'onlyContainsUserCerts, leaf has no basicConstraints',
    LEAF_NO_BASIC_CONSTRAINTS, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(issuingDistributionPoint_with_onlyContainsUserCerts)s
    }
  }
''' % crl_strings))


Store(
    'good_idp_onlycontainscacerts',
    'CA_NEW_BY_OLD covered by CRLs and not revoked, CRL has IDP with '
    'onlyContainsCaCerts',
    CA_NEW_BY_OLD, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(issuingDistributionPoint_with_onlyContainsCACerts)s
    }
  }
''' % crl_strings))


Store(
    'good_idp_uri_and_onlycontainsusercerts',
    'Leaf covered by CRLs and not revoked, CRL has IDP with URI and '
    'onlyContainsUserCerts',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(issuingDistributionPoint_with_uri_and_onlyContainsUserCerts)s
    }
  }
''' % crl_strings))


Store(
    'good_idp_uri_and_onlycontainscacerts',
    'CA_NEW_BY_OLD covered by CRLs and not revoked, CRL has IDP with URI and '
    'onlyContainsCACerts',
    CA_NEW_BY_OLD, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(issuingDistributionPoint_with_uri_and_onlyContainsCACerts)s
    }
  }
''' % crl_strings))


Store(
    'good_no_crldp',
    'Leaf covered by CRLs and not revoked and has no crlDistributionPoints.\n'
    'This tests the case where CheckCRL is called with a synthesized '
    'distributionPoint.',
    LEAF_NO_CRLDP, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'good_key_rollover',
    "Leaf issued by CA's new key but CRL is signed by old key",
    LEAF_BY_NEW, CA_NEW_BY_OLD, ca2=CA,
    crl_der=SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'revoked',
    'Leaf is revoked',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  %(leaf_revoked)s
  # no crlExtensions
''' % crl_strings))


Store(
    'revoked_no_nextupdate',
    'Leaf is revoked, optional nextUpdate field is absent',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  # no nextUpdate
  %(leaf_revoked)s
  # no crlExtensions
''' % crl_strings))


Store(
    'revoked_fake_crlentryextension',
    'Leaf is revoked, has non-critical crlEntryExtension',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  %(leaf_revoked_fake_extension)s
  # no crlExtensions
''' % crl_strings))


Store(
    'revoked_generalized_revocationdate',
    'Leaf is revoked, revocationDate is encoded as GeneralizedTime',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  %(leaf_revoked_generalizedtime)s
  # no crlExtensions
''' % crl_strings))


Store(
    'revoked_key_rollover',
    "Leaf issued by CA's new key but CRL is signed by old key",
    LEAF_BY_NEW, CA_NEW_BY_OLD, ca2=CA,
    crl_der=SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  SEQUENCE {
    SEQUENCE {
      INTEGER { %(LEAF_SERIAL)i }
      UTCTime { "170201001122Z" }
      # no crlEntryExtensions
    }
  }
  # no crlExtensions
''' % DictUnion(crl_strings,
                {'LEAF_SERIAL':LEAF_BY_NEW['cert'].get_serial_number()})))


Store(
    'bad_crldp_has_crlissuer',
    'Leaf covered by CRLs and not revoked, leaf has crlDistributionPoints '
    'with a crlIssuer',
    LEAF_CRLDP_CRLISSUER, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'bad_fake_critical_extension',
    'Leaf covered by CRLs and not revoked, but CRL has an unhandled critical '
    'extension',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  # no nextUpdate
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(fake_critical_extension)s
    }
  }
''' % crl_strings))


Store(
    'bad_fake_critical_crlentryextension',
    'Leaf is revoked, but a later entry has a critical crlEntryExtension',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  %(leaf_revoked_before_fake_critical_extension)s
  # no crlExtensions
''' % crl_strings))


Store(
    'bad_signature',
    'No revoked certs, but CRL signed by a different key',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings, signer=OTHER_CA))


Store(
    'bad_thisupdate_in_future',
    'Leaf covered by CRLs and not revoked, but thisUpdate is in the future',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate_in_future)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'bad_thisupdate_too_old',
    'Leaf covered by CRLs and not revoked, but thisUpdate time is more than '
    '7 days before verification time',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate_too_old)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'bad_nextupdate_too_old',
    'Leaf covered by CRLs and not revoked, but nextUpdate time is before '
    'verification time',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate_too_old)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'bad_wrong_issuer',
    'issuer name in CRL is different',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  SEQUENCE {
    SET {
      SEQUENCE {
        # commonName
        OBJECT_IDENTIFIER { 2.5.4.3 }
        PrintableString { "Test Unrelated CA" }
      }
    }
  }
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'bad_key_rollover_signature',
    "Leaf issued by CA's new key which is signed by old key, but CRL isn't "
    "signed by either",
    LEAF_BY_NEW, CA_NEW_BY_OLD, ca2=CA,
    crl_der=SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings, signer=OTHER_CA))


Store(
    'bad_idp_contains_wrong_uri',
    'Leaf not covered by CRL (IDP with different URI)',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(issuingDistributionPoint_wrong_uri)s
    }
  }
''' % crl_strings))


Store(
    'bad_idp_indirectcrl',
    'CRL IDP name matches, but has indirectCRL flag set',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(issuingDistributionPoint_with_indirectCRL)s
    }
  }
''' % crl_strings))


Store(
    'bad_idp_onlycontainscacerts',
    'Leaf not covered by CRLs because IDP has onlyContainsCACerts',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(issuingDistributionPoint_with_onlyContainsCACerts)s
    }
  }
''' % crl_strings))


Store(
    'bad_idp_onlycontainscacerts_no_basic_constraints',
    'Leaf not covered by CRLs because IDP has onlyContainsCACerts, '
    'leaf has no basicConstraints',
    LEAF_NO_BASIC_CONSTRAINTS, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(issuingDistributionPoint_with_onlyContainsCACerts)s
    }
  }
''' % crl_strings))


Store(
    'bad_idp_onlycontainsusercerts',
    'CA_NEW_BY_OLD not covered by CRLs because IDP has '
    'onlyContainsUserCerts',
    CA_NEW_BY_OLD, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(issuingDistributionPoint_with_onlyContainsUserCerts)s
    }
  }
''' % crl_strings))


Store(
    'bad_idp_uri_and_onlycontainsusercerts',
    'CA_NEW_BY_OLD not covered by CRLs because IDP has '
    'onlyContainsUserCerts (and URI, but the URI matches)',
    CA_NEW_BY_OLD, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(issuingDistributionPoint_with_uri_and_onlyContainsUserCerts)s
    }
  }
''' % crl_strings))


Store(
    'bad_idp_uri_and_onlycontainscacerts',
    'Leaf not covered by CRLs because IDP has '
    'onlyContainsCACerts (and URI, but the URI matches)',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(issuingDistributionPoint_with_uri_and_onlyContainsCACerts)s
    }
  }
''' % crl_strings))


Store(
    'invalid_mismatched_signature_algorithm',
    'Leaf covered by CRLs and not revoked, but signatureAlgorithm in '
    'CertificateList does not match the one in TBSCertList.',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha384WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'invalid_revoked_empty_sequence',
    'revokedCertificates is an empty sequence (should be omitted)',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  SEQUENCE {
    # no revoked certs. revokedCertificates should be omitted in this case.
  }
  # no crlExtensions
''' % crl_strings))


Store(
    'invalid_v1_with_extension',
    'CRL is V1 and has crlExtensions',
    LEAF, CA,
    SignAsciiCRL('''
  # no version
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  # no nextUpdate
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(fake_extension)s
    }
  }
''' % crl_strings))


Store(
    'invalid_v1_with_crlentryextension',
    'Leaf is revoked, has non-critical crlEntryExtension, but CRL is V1',
    LEAF, CA,
    SignAsciiCRL('''
  # no version
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  %(leaf_revoked_fake_extension)s
  # no crlExtensions
''' % crl_strings))


Store(
    'invalid_v1_explicit',
    'CRL has explicit V1 version',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 0 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'invalid_v3',
    'CRL has invalid V3 version',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 2 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'invalid_issuer_keyusage_no_crlsign',
    'Leaf covered by CRLs and not revoked, issuer has keyUsage extension '
    'without the cRLSign bit set',
    LEAF, CA_KEYUSAGE_NOCRLSIGN,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings, signer=CA_KEYUSAGE_NOCRLSIGN))


Store(
    'invalid_key_rollover_issuer_keyusage_no_crlsign',
    "Leaf issued by CA's new key but CRL is signed by old key, and the old "
    "key cert has keyUsage extension without the cRLSign bit set",
    LEAF_BY_NEW, CA_NEW_BY_OLD, ca2=CA_KEYUSAGE_NOCRLSIGN,
    crl_der=SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings, signer=CA_KEYUSAGE_NOCRLSIGN))


Store(
    'invalid_garbage_version',
    'CRL version is garbage',
    LEAF, CA,
    SignAsciiCRL('''
  OCTET_STRING { `01` }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'invalid_garbage_tbs_signature_algorithm',
    'CRL tbs signature algorithm is garbage',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  INTEGER { 1 }
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'invalid_garbage_issuer_name',
    'CRL issuer is garbage',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  INTEGER { 1 }
  %(thisUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'invalid_garbage_thisupdate',
    'CRL thisUpdate is garbage',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  INTEGER { 1 }
  %(thisUpdate)s
  # no revoked certs list
  # no crlExtensions
''' % crl_strings))


Store(
    'invalid_garbage_after_thisupdate',
    'CRL garbage after thisupdate',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  # garbage:
  INTEGER { 1 }
''' % crl_strings))


Store(
    'invalid_garbage_after_nextupdate',
    'CRL garbage after nextUpdate',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # garbage:
  INTEGER { 1 }
''' % crl_strings))


Store(
    'invalid_garbage_after_revokedcerts',
    'CRL garbage after revokedCertificates',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  # no nextUpdate
  %(leaf_revoked)s
  # no crlExtensions
  # garbage: nextUpdate doesn't go here:
  %(nextUpdate)s
''' % crl_strings))


Store(
    'invalid_garbage_after_extensions',
    'CRL garbage after extensions',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(fake_extension)s
    }
  }
  # Garbage: revoked certs sequence doesn't go here:
  %(leaf_revoked)s
''' % crl_strings))


Store(
    'invalid_garbage_tbscertlist',
    'CRL garbage tbsCertList',
    LEAF, CA,
    Ascii2Der('''
SEQUENCE {
  OCTET_STRING { `5678` }
  SEQUENCE {
    # sha256WithRSAEncryption
    OBJECT_IDENTIFIER { 1.2.840.113549.1.1.11 }
    NULL {}
  }
  # Actual signatureValue doesn't matter, shouldn't get to verifying signature.
  BIT_STRING { `001a` }
}
'''))


Store(
    'invalid_garbage_signaturealgorithm',
    'CRL garbage signatureAlgorithm',
    LEAF, CA,
    Ascii2Der('''
SEQUENCE {
  SEQUENCE {
    INTEGER { 1 }
    # tbsCertList contents doesn't matter, parsing shouldn't get this far.
  }
  OCTET_STRING { `5678` }
  # Actual signatureValue doesn't matter, shouldn't get to verifying signature.
  BIT_STRING { `001a` }
}
'''))


Store(
    'invalid_garbage_signaturevalue',
    'CRL garbage signatureValue',
    LEAF, CA,
    Ascii2Der('''
SEQUENCE {
  SEQUENCE {
    INTEGER { 1 }
    # tbsCertList contents doesn't matter, parsing shouldn't get this far.
  }
  SEQUENCE {
    # sha256WithRSAEncryption
    OBJECT_IDENTIFIER { 1.2.840.113549.1.1.11 }
    NULL {}
  }
  # Actual signatureValue contents don't matter, should be BIT_STRING rather
  # than OCTET_STRING.
  OCTET_STRING { `001a` }
}
'''))


Store(
    'invalid_garbage_after_signaturevalue',
    'CRL garbage after signatureValue',
    LEAF, CA,
    Ascii2Der('''
SEQUENCE {
  SEQUENCE {
    INTEGER { 1 }
    # tbsCertList contents doesn't matter, parsing shouldn't get this far.
  }
  SEQUENCE {
    # sha256WithRSAEncryption
    OBJECT_IDENTIFIER { 1.2.840.113549.1.1.11 }
    NULL {}
  }
  # Actual signatureValue doesn't matter, shouldn't get to verifying signature.
  BIT_STRING { `001a` }
  SEQUENCE {}
}
'''))

Store(
    'invalid_garbage_revoked_serial_number',
    'Leaf is revoked but a following crlentry is garbage',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
    SEQUENCE {
      SEQUENCE {
        INTEGER { %(LEAF_SERIAL)i }
        UTCTime { "170201001122Z" }
        # no crlEntryExtensions
      }
      SEQUENCE {
        OCTET_STRING { `7F`}
        UTCTime { "170201001122Z" }
        # no crlEntryExtensions
      }
    }
  # no crlExtensions
''' % (DictUnion(crl_strings,
                 {'LEAF_SERIAL':LEAF['cert'].get_serial_number()}))))


Store(
    'invalid_garbage_revocationdate',
    'Leaf is revoked but a following crlentry is garbage',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
    SEQUENCE {
      SEQUENCE {
        INTEGER { %(LEAF_SERIAL)i }
        UTCTime { "170201001122Z" }
        # no crlEntryExtensions
      }
      SEQUENCE {
        INTEGER { 100001 }
        OCTET_STRING { "170201001122Z" }
        # no crlEntryExtensions
      }
    }
  # no crlExtensions
''' % (DictUnion(crl_strings,
                 {'LEAF_SERIAL':LEAF['cert'].get_serial_number()}))))


Store(
    'invalid_garbage_after_revocationdate',
    'Leaf is revoked but a following crlentry is garbage',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
    SEQUENCE {
      SEQUENCE {
        INTEGER { %(LEAF_SERIAL)i }
        UTCTime { "170201001122Z" }
        # no crlEntryExtensions
      }
      SEQUENCE {
        INTEGER { 100001 }
        UTCTime { "170201001122Z" }
        INTEGER { 01 }
      }
    }
  # no crlExtensions
''' % (DictUnion(crl_strings,
                 {'LEAF_SERIAL':LEAF['cert'].get_serial_number()}))))


Store(
    'invalid_garbage_after_crlentryextensions',
    'Leaf is revoked but a following crlentry is garbage',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
    SEQUENCE {
      SEQUENCE {
        INTEGER { %(LEAF_SERIAL)i }
        UTCTime { "170201001122Z" }
        # no crlEntryExtensions
      }
      SEQUENCE {
        INTEGER { 100001 }
        UTCTime { "170201001122Z" }
        SEQUENCE {
          SEQUENCE {
            OBJECT_IDENTIFIER { 1.2.3.4 }
            OCTET_STRING { `5678` }
          }
        }
        INTEGER { 01 }
      }
    }
  # no crlExtensions
''' % (DictUnion(crl_strings,
                 {'LEAF_SERIAL':LEAF['cert'].get_serial_number()}))))


Store(
    'invalid_garbage_crlentry',
    'Leaf is revoked but a following crlentry is garbage',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
    SEQUENCE {
      SEQUENCE {
        INTEGER { %(LEAF_SERIAL)i }
        UTCTime { "170201001122Z" }
        # no crlEntryExtensions
      }
      INTEGER { 01 }
    }
  # no crlExtensions
''' % (DictUnion(crl_strings,
                 {'LEAF_SERIAL':LEAF['cert'].get_serial_number()}))))


Store(
    'invalid_idp_dpname_choice_extra_data',
    'IssuingDistributionPoint extension distributionPoint is invalid',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      SEQUENCE {
        OBJECT_IDENTIFIER { 2.5.29.28 }
        BOOLEAN { `ff` }
        OCTET_STRING {
          SEQUENCE {
            [0] {
              [0] {
                [6 PRIMITIVE] { "http://example.com/foo.crl" }
              }
              [1] {
                SET {
                  SEQUENCE {
                    # countryName
                    OBJECT_IDENTIFIER { 2.5.4.6 }
                    PrintableString { "US" }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
''' % crl_strings))


Store(
    'invalid_idp_empty_sequence',
    'IssuingDistributionPoint extension is invalid',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      SEQUENCE {
        OBJECT_IDENTIFIER { 2.5.29.28 }
        BOOLEAN { `ff` }
        OCTET_STRING {
          SEQUENCE {
          }
        }
      }
    }
  }
''' % crl_strings))


Store(
    'invalid_idp_onlycontains_user_and_ca_certs',
    'IssuingDistributionPoint extension is invalid, cannot specify more than '
    'one of onlyContainsUserCerts and onlyContainsCACerts',
    LEAF, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      SEQUENCE {
        OBJECT_IDENTIFIER { 2.5.29.28 }
        BOOLEAN { `ff` }
        OCTET_STRING {
          SEQUENCE {
           [1 PRIMITIVE] { `ff` }
           [2 PRIMITIVE] { `ff` }
          }
        }
      }
    }
  }
''' % crl_strings))


Store(
    'invalid_idp_onlycontainsusercerts_v1_leaf',
    'v1 leaf is covered by CRL with onlyContainsUserCerts, which is invalid',
    LEAF_V1, CA,
    SignAsciiCRL('''
  INTEGER { 1 }
  %(sha256WithRSAEncryption)s
  %(CA_name)s
  %(thisUpdate)s
  %(nextUpdate)s
  # no revoked certs list
  [0] {
    SEQUENCE {
      %(issuingDistributionPoint_with_onlyContainsUserCerts)s
    }
  }
''' % crl_strings))