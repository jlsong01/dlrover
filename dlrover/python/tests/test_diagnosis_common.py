# Copyright 2024 The DLRover Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import unittest

from dlrover.python.common.diagnosis import node_failed


class DiagnosisTest(unittest.TestCase):
    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_should_relaunch_worker(self):
        file = "data/training.log"
        path = os.path.dirname(__file__)
        file_path = os.path.join(path, file)
        self.assertTrue(node_failed(file_path))