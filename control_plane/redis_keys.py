STREAM_RESULTS = "omega:stream:results"
STREAM_ACCEPTED = "omega:stream:accepted"
TREE_INDEX = "omega:trees"
RECENT_RUNS = "omega:recent_runs"
STATS_TOTAL_EVALS = "omega:stats:total_evals"
STATS_INFLIGHT = "omega:stats:inflight"
STATS_EVAL_TIMES = "omega:stats:eval_times"
WORKER_HEARTBEATS = "omega:workers"


def root_node_id(task_id: str) -> str:
    return f"omega:tree:{task_id}:root"


def tree_node_id(task_id: str, digest: str) -> str:
    return f"omega:tree:{task_id}:{digest}"


def node_children_key(node_id: str) -> str:
    return f"{node_id}:children"


def node_lock_key(node_id: str) -> str:
    return f"omega:lock:{node_id}"


def tree_done_key(task_id: str) -> str:
    return f"omega:tree:{task_id}:done"


def tree_accepted_key(task_id: str) -> str:
    return f"omega:tree:{task_id}:accepted"
