"""Endpoint selection is pure and never retries a remote command."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from lifecycle import CloudError,ssh_endpoint

class SSH(unittest.TestCase):
    def test_native_direct_mapping_takes_precedence(self):
        self.assertEqual(ssh_endpoint({'public_ipaddr':'203.0.113.9','ports':{'22/tcp':[{'HostPort':'2222'}]},'ssh_host':'proxy.example','ssh_port':3333}),('203.0.113.9',2222))
    def test_direct_does_not_require_proxy_fields(self):
        self.assertEqual(ssh_endpoint({'public_ipaddr':'203.0.113.9','ports':{'22/tcp':[{'HostPort':'2222'}]}}),('203.0.113.9',2222))
    def test_missing_or_invalid_direct_endpoint_falls_back_to_proxy(self):
        for mapping in [None,{}, {'22/tcp':[]},{'22/tcp':[{'HostPort':'invalid'}]}]:
            self.assertEqual(ssh_endpoint({'ports':mapping,'public_ipaddr':'203.0.113.9','ssh_host':'proxy.example','ssh_port':3333}),('proxy.example',3333))
    def test_invalid_endpoint_is_rejected(self):
        for row in [{},{'ssh_host':'bad host','ssh_port':22},{'ssh_host':'proxy.example','ssh_port':70000}]:
            with self.assertRaises(CloudError):ssh_endpoint(row)

if __name__=='__main__':unittest.main()
