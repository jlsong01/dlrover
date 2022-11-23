# Copyright 2022 The DLRover Authors. All rights reserved.
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
import time

from dlrover.python.common.constants import DistributionStrategy, JobExitReason
from dlrover.python.common.log_utils import default_logger as logger
from dlrover.python.elastic_training.elastic_ps import ElasticPsService
from dlrover.python.master.monitor.speed_monitor import SpeedMonitor
from dlrover.python.master.node_manager.event_callback import (
    TaskRescheduleCallback,
)
from dlrover.python.master.node_manager.node_manager import create_node_manager
from dlrover.python.master.servicer import create_master_service
from dlrover.python.master.shard_manager.task_manager import TaskManager
from dlrover.python.master.stats_collector.job_collector import (
    JobMetricCollector,
)


def _create_rendezvous_server_if_needed(args):
    master_ip = os.getenv("MY_POD_IP", "localhost")
    if args.use_ddp:
        logger.info("call DDPRendezvousServer, master_ip:{}".format(master_ip))
        return None
    elif args.distribution_strategy != DistributionStrategy.ALLREDUCE:
        return None
    else:
        logger.info(
            "call HorovodRendezvousServer, master_ip:{}".format(master_ip)
        )
        return None


def _create_elastic_ps_service_if_needed(args):
    if args.distribution_strategy == DistributionStrategy.PARAMETER_SERVER:
        return ElasticPsService()
    return None


class Master(object):
    def __init__(self, args):
        self.speed_monitor = SpeedMonitor()
        self.node_manager = (
            create_node_manager(args, self.speed_monitor)
            if args.need_node_manager
            else None
        )
        self.task_manager = (
            TaskManager(args.relaunch_timeout_worker, self.speed_monitor)
            if args.need_task_manager
            else None
        )
        self.rendezvous_server = _create_rendezvous_server_if_needed(args)
        self.job_metric_collector = self._create_metric_collector_if_needed(
            args
        )
        self.elastic_ps_service = _create_elastic_ps_service_if_needed(args)
        self._master_server = self._create_master_grpc_service(args)
        self._args = args
        self._stop_requested = False
        self._exit_code = 0
        self._exit_reason = None

    def _create_master_grpc_service(self, args):
        return create_master_service(
            args.port,
            self.task_manager,
            self.node_manager,
            self.speed_monitor,
            self.rendezvous_server,
            self.job_metric_collector,
            self.elastic_ps_service,
        )

    def _create_metric_collector_if_needed(self, args):
        if not args.need_node_manager:
            return None
        job_uuid = self.node_manager.get_job_uuid()
        return JobMetricCollector(
            job_uuid, args.namespace, args.cluster, args.user
        )

    def prepare(self):
        # Composite the components
        if self.task_manager and self.node_manager:
            self.task_manager.set_task_timeout_callback(
                self.node_manager.remove_worker
            )
        if self.node_manager:
            self._add_pod_event_callback()

        # Start the components one by one
        if self.task_manager:
            self.task_manager.start()
        if self.rendezvous_server:
            self.rendezvous_server.start()
        if self.node_manager:
            self.node_manager.start()
        if self.job_metric_collector:
            self.job_metric_collector.report_job_type(
                self._args.distribution_strategy
            )

        # Start the master GRPC server
        logger.info("Starting master RPC server")
        self._master_server.start()
        logger.info("Master RPC server started")

    def _add_pod_event_callback(self):
        # Add PodEventCallbacks for the listeners of Pod events.
        if self.task_manager:
            self.node_manager.add_pod_event_callback(
                TaskRescheduleCallback(self.task_manager)
            )

    def run(self):
        """
        The main loop of master.
        Dispatch the tasks to the workers until all the tasks are completed.
        """
        try:
            while True:
                if self._stop_requested:
                    break
                if (
                    self.node_manager
                    and self.node_manager.all_workers_exited()
                ):
                    if self.node_manager.all_workers_failed():
                        logger.error("All workers failed")
                        self._exit_code = 1
                        self._exit_reason = JobExitReason.UNKNOWN_ERROR
                        break

                    if self.task_manager and not self.task_manager.finished():
                        logger.warning(
                            "All workers exited but there also are "
                            "unfinished tasks",
                        )
                    break

                if (
                    self.task_manager
                    and self.task_manager.finished()
                    and (
                        not self.node_manager
                        or self.node_manager.all_critical_node_completed()
                    )
                ):
                    logger.info("All task completed")
                    break

                time.sleep(30)
        except KeyboardInterrupt:
            logger.warning("Server stopping")
        finally:
            if self.node_manager:
                self.node_manager.stop()
            self.stop()

        return self._exit_code

    def stop(self):
        """
        Stop all the components.
        Make sure that the created services and components are shut down.
        """
        if self._exit_code == 0 and not self._exit_reason:
            self._exit_reason = JobExitReason.SUCCEEDED
        logger.info("Job exit with the reason {}".format(self._exit_reason))
        if self.job_metric_collector:
            self.job_metric_collector.report_job_exit_reason_to_easydl(
                self._exit_reason
            )
        logger.info("Stopping master")
        logger.info("Stopping RPC server")
        self._master_server.stop(grace=None)
        logger.info("RPC server stopped")
        logger.info("Master stopped")

    def request_stop(self, success, reason, msg=""):
        self._stop_requested = True
        self._exit_reason = reason
        if success:
            self._exit_code = 0
            logger.info(msg)
        else:
            self._exit_code = 1
            logger.error(msg)
