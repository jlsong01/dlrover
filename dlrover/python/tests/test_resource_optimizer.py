# Copyright 2022 The EasyDL Authors. All rights reserved.
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

import unittest

from dlrover.proto import brain_pb2
from dlrover.python.brain.client import build_easydl_client
from dlrover.python.common.constants import NodeResourceLimit, NodeType
from dlrover.python.common.global_context import Context
from dlrover.python.common.node import Node, NodeGroupResource, NodeResource
from dlrover.python.master.resource.brain_optimizer import (
    BrainResoureOptimizer,
)
from dlrover.python.master.resource.job import (
    JobResource,
    JobResourceOptimizer,
)
from dlrover.python.master.resource.optimizer import ResourcePlan

_dlrover_context = Context.instance()
_MEMORY = 8192


class MockStub(object):
    def optimize(self, request):
        res = brain_pb2.OptimizeResponse()
        res.job_optimize_plans.add()
        group_resources = res.job_optimize_plans[
            0
        ].resource.task_group_resources
        group_resources[NodeType.WORKER].count = 5
        group_resources[NodeType.WORKER].resource.memory = (
            _MEMORY * 1024 * 1024
        )
        group_resources[NodeType.WORKER].resource.cpu = 16

        group_resources[NodeType.PS].count = 2
        group_resources[NodeType.PS].resource.memory = _MEMORY * 1024 * 1024
        group_resources[NodeType.PS].resource.cpu = 16
        return res


class ResourceOptimizerTest(unittest.TestCase):
    def test_brain_optimizer(self):
        optimizer = BrainResoureOptimizer("1111")
        optimizer._brain_client = build_easydl_client()
        optimizer._brain_client._processor_stub = MockStub()
        plan: ResourcePlan = optimizer.generate_opt_plan("", {})
        worker = plan.node_group_resources[NodeType.WORKER]
        self.assertEqual(worker.count, 5)
        self.assertEqual(worker.node_resource.cpu, 16)
        self.assertEqual(worker.node_resource.memory, _MEMORY)

        ps = plan.node_group_resources[NodeType.PS]
        self.assertEqual(ps.count, 2)
        self.assertEqual(ps.node_resource.cpu, 16)
        self.assertEqual(ps.node_resource.memory, _MEMORY)

    def test_limit_resource_plan(self):
        plan = ResourcePlan()
        plan.node_group_resources[NodeType.WORKER] = NodeGroupResource(
            1,
            NodeResource(1, 256),
        )
        plan.node_group_resources[NodeType.PS] = NodeGroupResource(
            1,
            NodeResource(64, 2560000),
        )
        plan.limit_resource_value()
        worker = plan.node_group_resources[NodeType.WORKER]
        self.assertEqual(worker.count, 1)
        self.assertEqual(
            worker.node_resource.cpu, NodeResourceLimit.MIN_CPU_CORES
        )
        self.assertEqual(
            worker.node_resource.memory, NodeResourceLimit.MIN_MEMORY
        )

        ps = plan.node_group_resources[NodeType.PS]
        self.assertEqual(ps.count, 1)
        self.assertEqual(ps.node_resource.cpu, NodeResourceLimit.MAX_CPU_CORES)
        self.assertEqual(ps.node_resource.memory, NodeResourceLimit.MAX_MEMORY)


class JobResourceOptimizerTest(unittest.TestCase):
    def setUp(self):
        self._client = build_easydl_client()
        self._client._processor_stub = MockStub()
        worker_resource = NodeGroupResource(
            0,
            NodeResource(0, 0),
        )
        ps_resource = NodeGroupResource(
            0,
            NodeResource(0, 0),
        )
        self._job_optimizer = JobResourceOptimizer(
            worker_resource, ps_resource, "brain", "aa0_uuid"
        )
        resource_optimizer = self._job_optimizer._resource_optimizer
        resource_optimizer._brain_client = self._client
        _dlrover_context.easydl_ps_enabled = True
        _dlrover_context.easydl_worker_enabled = True

    def test_fixed_resource(self):
        worker_resource = NodeGroupResource(
            1,
            NodeResource(0, 1024),
        )
        ps_resource = NodeGroupResource(
            1,
            NodeResource(4, 1024),
        )
        self._job_optimizer = JobResourceOptimizer(
            worker_resource, ps_resource, "brain", "aa0_uuid"
        )
        resource_optimizer = self._job_optimizer._resource_optimizer
        resource_optimizer._brain_client = self._client
        self._job_optimizer._init_job_resource_by_optimizer()
        worker = self._job_optimizer._worker_resource
        self.assertEqual(
            worker.node_resource.memory,
            1024,
        )
        self.assertEqual(worker.node_resource.cpu, 16)
        self.assertEqual(worker.count, 1)
        ps = self._job_optimizer._ps_resource
        self.assertEqual(ps.node_resource.memory, 1024)
        self.assertEqual(ps.node_resource.cpu, 4)
        self.assertEqual(ps.count, 1)

    def test_init_resource_by_optimizer(self):
        self._job_optimizer._init_job_resource_by_optimizer()
        worker = self._job_optimizer._worker_resource
        self.assertEqual(
            worker.node_resource.memory,
            _MEMORY,
        )
        self.assertEqual(worker.node_resource.cpu, 16)
        self.assertEqual(worker.count, 5)
        ps = self._job_optimizer._ps_resource
        self.assertEqual(ps.node_resource.memory, _MEMORY)
        self.assertEqual(ps.node_resource.cpu, 16)
        self.assertEqual(ps.count, 2)

    def test_optimize_worker_resource(self):
        self._job_optimizer.optimize_worker_resource()
        worker = self._job_optimizer._worker_resource
        self.assertEqual(worker.node_resource.memory, _MEMORY)

    def test_init_job_resource(self):
        job = JobResource()
        job.add_node_group_resource(
            NodeType.PS, 3, "cpu=1,memory=256Mi", "high"
        )
        job.add_node_group_resource(
            NodeType.WORKER, 0, "cpu=1,memory=2564Mi", "high"
        )
        job.add_node_group_resource(NodeType.EVALUATOR, 5, "", "high")
        job = self._job_optimizer.init_job_resource(job)
        self.assertEqual(job.worker_num, 5)
        self.assertEqual(job.ps_num, 2)
        worker = job.get_node_group_resource(NodeType.WORKER)
        self.assertEqual(worker.node_resource.memory, _MEMORY)
        self.assertEqual(worker.node_resource.cpu, 16)

        evaluator = job.get_node_group_resource(NodeType.EVALUATOR)
        self.assertEqual(evaluator.node_resource.memory, _MEMORY)
        self.assertEqual(evaluator.node_resource.cpu, 16)

        ps = job.get_node_group_resource(NodeType.PS)
        self.assertEqual(ps.node_resource.memory, _MEMORY)
        self.assertEqual(ps.node_resource.cpu, 16)

    def test_update_worker_resource_from_easydl(self):
        worker_resource = NodeGroupResource(5, NodeResource(0, 0))
        ps_resource = NodeGroupResource(0, NodeResource(0, 0))
        job_optimizer = JobResourceOptimizer(
            worker_resource, ps_resource, "brain", "aa0_uuid"
        )
        job_optimizer._resource_optimizer._brain_client = self._client
        job_optimizer._init_job_resource_by_optimizer()
        worker = job_optimizer._worker_resource
        self.assertEqual(worker.count, 5)
        self.assertEqual(worker.node_resource.memory, _MEMORY)
        self.assertEqual(worker.node_resource.cpu, 16)

    def test_adjust_oom_ps_resource(self):
        worker_resource = NodeGroupResource(10, NodeResource(1, 256))
        ps_resource = NodeGroupResource(3, NodeResource(2, 1024))
        job_optimizer = JobResourceOptimizer(
            worker_resource, ps_resource, "brain", "aa0_uuid"
        )

        job_optimizer._resource_optimizer._brain_client = self._client
        oom_ps = Node(
            "ps", 0, name="ps-0", config_resource=NodeResource(2, 1024)
        )
        plan: ResourcePlan = job_optimizer.adjust_oom_ps_resource(
            oom_ps, False
        )
        self.assertFalse(plan.empty())
        ps = plan.node_group_resources[NodeType.PS]
        self.assertEqual(ps.count, 3)

        plan = job_optimizer.adjust_oom_ps_resource(oom_ps, True)
        self.assertTrue(plan.empty())
        self.assertEqual(oom_ps.config_resource.memory, 8192)
