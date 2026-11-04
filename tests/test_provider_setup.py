"""Provider-layer metadata becomes useful, safe boot-stage text."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from lifecycle import provider_boot_message

class BootStage(unittest.TestCase):
    def test_latest_docker_layer_phase_is_shown_without_raw_provider_data(self):
        value=provider_boot_message({'status_msg':'public-layer: Verifying Checksum\npublic-layer: Download complete'})
        self.assertIn('another layer finished',value);self.assertNotIn('public-layer',value)
    def test_only_controlled_stage_messages_are_exposed(self):
        for raw,expected in [('layer: Extracting','Unpacking'),('layer: Verifying Checksum','Verifying'),('layer: Pull complete','Assembling'),('layer: Downloading','Downloading'),('unrecognized provider diagnostic','Waiting')]:
            self.assertTrue(provider_boot_message({'status_msg':raw}).startswith(expected))

if __name__=='__main__':unittest.main()
