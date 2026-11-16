"""A small Hadoop cluster (HDFS + YARN) of separate daemons: `python -m corpus_jobs.cluster`.

    python -m corpus_jobs.cluster start --workers 2 --dir /tmp/cj-cluster
    export HADOOP_CONF_DIR=/tmp/cj-cluster/conf-client
    python -m corpus_jobs.bench --scenarios average --engines hadoop hadoop-lsh \
        --hdfs hdfs://localhost:9000/user/cj --out results/project/cluster_bench.json
    python -m corpus_jobs.cluster stop --dir /tmp/cj-cluster

`start` writes a configuration directory for each daemon, formats a new NameNode, and starts one
NameNode, one ResourceManager, and for each worker one DataNode and one NodeManager. Every
daemon is its own Java process with its own ports, data directories, PID file and logs, so the
jobs run through the real HDFS and YARN code: map output crosses from one NodeManager's shuffle
service to another, blocks are replicated across DataNodes, and tasks run in YARN containers.

All daemons run on this machine. Each worker offers `--worker-vcores` cores and
`--worker-memory` MB to YARN, and they share the same physical cores, so this cluster checks
that the jobs run correctly on HDFS and YARN and measures what the cluster machinery costs. It
cannot show how the jobs scale across machines. To run on several machines, give each machine
the worker's configuration (with fs.defaultFS and the ResourceManager address pointing at the
master), and ship the `corpus_jobs` package with the job, because the tasks import it from this
checkout (`PYTHONPATH`).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import time
from pathlib import Path
from xml.sax.saxutils import escape

NN_PORT, RM_HOST = 9000, "localhost"


def xml(props: dict) -> str:
    rows = "".join(f"<property><name>{escape(k)}</name><value>{escape(str(v))}</value></property>"
                   for k, v in props.items())  # fmt: skip
    return f'<?xml version="1.0"?>\n<configuration>{rows}</configuration>\n'


def common(root: Path, workers: int, memory: int, vcores: int) -> dict[str, dict]:
    home = os.environ["HADOOP_HOME"]
    mr_env = f"HADOOP_MAPRED_HOME={home}"
    return {
        "core-site.xml": {"fs.defaultFS": f"hdfs://localhost:{NN_PORT}",
                          "hadoop.tmp.dir": str(root / "tmp")},
        "hdfs-site.xml": {"dfs.replication": min(2, workers),
                          "dfs.namenode.name.dir": str(root / "nn"),
                          "dfs.permissions.enabled": "false",
                          "dfs.namenode.http-address": "localhost:9870"},
        "yarn-site.xml": {
            "yarn.resourcemanager.hostname": RM_HOST,
            "yarn.nodemanager.aux-services": "mapreduce_shuffle",
            "yarn.nodemanager.pmem-check-enabled": "false",  # Python tasks run beside the JVM
            "yarn.nodemanager.vmem-check-enabled": "false",
            "yarn.nodemanager.resource.memory-mb": memory,
            "yarn.nodemanager.resource.cpu-vcores": vcores,
            "yarn.scheduler.minimum-allocation-mb": 256,
            "yarn.scheduler.maximum-allocation-mb": memory,
            "yarn.log-aggregation-enable": "false",
        },
        "mapred-site.xml": {
            "mapreduce.framework.name": "yarn",
            "yarn.app.mapreduce.am.env": mr_env,
            "mapreduce.map.env": mr_env,
            "mapreduce.reduce.env": mr_env,
            "yarn.app.mapreduce.am.resource.memory-mb": 640,
            "yarn.app.mapreduce.am.command-opts": "-Xmx448m",
            "mapreduce.map.memory.mb": 896,
            "mapreduce.map.java.opts": "-Xmx640m",
            "mapreduce.reduce.memory.mb": 1152,
            "mapreduce.reduce.java.opts": "-Xmx896m",
        },
    }  # fmt: skip


def worker(i: int, root: Path) -> dict[str, dict]:
    """Ports and directories of worker i (1-based): its DataNode and NodeManager."""
    p = 20000 + 100 * i
    return {
        "hdfs-site.xml": {"dfs.datanode.data.dir": str(root / f"dn{i}"),
                          "dfs.datanode.address": f"localhost:{p}",
                          "dfs.datanode.http.address": f"localhost:{p + 1}",
                          "dfs.datanode.ipc.address": f"localhost:{p + 2}"},
        "yarn-site.xml": {"yarn.nodemanager.address": f"localhost:{p + 10}",
                          "yarn.nodemanager.localizer.address": f"localhost:{p + 11}",
                          "yarn.nodemanager.webapp.address": f"localhost:{p + 12}",
                          "yarn.nodemanager.local-dirs": str(root / f"nm{i}" / "local"),
                          "yarn.nodemanager.log-dirs": str(root / f"nm{i}" / "logs")},
        "mapred-site.xml": {"mapreduce.shuffle.port": p + 20},
    }  # fmt: skip


def write_conf(target: Path, base: dict[str, dict], extra: dict[str, dict] | None = None) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    for name, props in base.items():
        (target / name).write_text(xml({**props, **(extra or {}).get(name, {})}))
    src = Path(os.environ["HADOOP_HOME"]) / "etc" / "hadoop"
    for f in ("log4j.properties", "hadoop-env.sh", "yarn-env.sh", "mapred-env.sh"):
        if (src / f).exists():
            shutil.copy(src / f, target / f)
    return target


def daemon(root: Path, conf: Path, name: str, tool: str, role: str, action: str,
           heap: str) -> None:  # fmt: skip
    env = {**os.environ, "HADOOP_CONF_DIR": str(conf), "HADOOP_PID_DIR": str(root / "pid" / name),
           "HADOOP_LOG_DIR": str(root / "log" / name), "HADOOP_HEAPSIZE_MAX": heap,
           "HDFS_NAMENODE_USER": "root", "HDFS_DATANODE_USER": "root",
           "YARN_RESOURCEMANAGER_USER": "root", "YARN_NODEMANAGER_USER": "root"}  # fmt: skip
    cmd = [str(Path(os.environ["HADOOP_HOME"]) / "bin" / tool), "--daemon", action, role]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode and action == "start":
        raise RuntimeError(f"{name}: {proc.stderr[-2000:]}")


def daemons(root: Path, workers: int):
    yield "namenode", root / "conf-client", "hdfs", "namenode", "512m"
    yield "resourcemanager", root / "conf-client", "yarn", "resourcemanager", "512m"
    for i in range(1, workers + 1):
        yield f"datanode{i}", root / f"conf-w{i}", "hdfs", "datanode", "256m"
        yield f"nodemanager{i}", root / f"conf-w{i}", "yarn", "nodemanager", "384m"


def start(a) -> None:
    root = Path(a.dir).resolve()
    if root.exists():
        shutil.rmtree(root)
    base = common(root, a.workers, a.worker_memory, a.worker_vcores)
    write_conf(root / "conf-client", base)
    for i in range(1, a.workers + 1):
        write_conf(root / f"conf-w{i}", base, worker(i, root))
    hdfs_bin = str(Path(os.environ["HADOOP_HOME"]) / "bin" / "hdfs")
    fmt = [hdfs_bin, "--config", str(root / "conf-client"), "namenode", "-format", "-force",
           "-nonInteractive"]  # fmt: skip
    subprocess.run(fmt, capture_output=True, text=True, check=True)
    for name, conf, tool, role, heap in daemons(root, a.workers):
        daemon(root, conf, name, tool, role, "start", heap)
    client = {**os.environ, "HADOOP_CONF_DIR": str(root / "conf-client")}
    yarn = str(Path(os.environ["HADOOP_HOME"]) / "bin" / "yarn")
    hdfs = str(Path(os.environ["HADOOP_HOME"]) / "bin" / "hdfs")
    for _ in range(90):  # wait until every DataNode and NodeManager has registered
        time.sleep(2)
        nodes = subprocess.run([yarn, "node", "-list"], env=client, capture_output=True,
                               text=True).stdout  # fmt: skip
        live = subprocess.run([hdfs, "dfsadmin", "-report", "-live"], env=client,
                              capture_output=True, text=True).stdout  # fmt: skip
        running = nodes.count("RUNNING")
        datanodes = live.count("Name: ")
        if running >= a.workers and datanodes >= a.workers:
            break
    else:
        raise RuntimeError(f"cluster did not come up: {running} NodeManagers, "
                           f"{datanodes} DataNodes; see {root / 'log'}")  # fmt: skip
    print(f"cluster up: {a.workers} workers ({running} NodeManagers, {datanodes} DataNodes)\n"
          f"export HADOOP_CONF_DIR={root / 'conf-client'}")  # fmt: skip


def stop(a) -> None:
    root = Path(a.dir).resolve()
    for name, conf, tool, role, heap in reversed(list(daemons(root, a.workers))):
        daemon(root, conf, name, tool, role, "stop", heap)
    print("cluster stopped")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m corpus_jobs.cluster", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    ap.add_argument("action", choices=("start", "stop"))
    ap.add_argument("--dir", default="/tmp/cj-cluster")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--worker-memory", type=int, default=2304, help="MB each worker offers YARN")
    ap.add_argument("--worker-vcores", type=int, default=1)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if "HADOOP_HOME" not in os.environ:
        raise SystemExit("set HADOOP_HOME to a Hadoop 3.x installation")
    start(a) if a.action == "start" else stop(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
