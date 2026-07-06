#!/usr/bin/env bash
# Shared parallelism + memory guards for DIMACS export/solve scripts.
#
# Env overrides:
#   BATCH_JOBS       parallel configs (run_batch_dimacs.sh), default 4
#   PATH_JOBS        parallel path solves per config, default 32
#   SOLVER_MEM_MB    RAM budget per solver worker (memfree gate), default 2048
#   EXPORT_MEM_MB    RAM budget per export worker (memfree gate), default 8192
#   MEM_RESERVE_MB   keep this much RAM free for OS, default 4096
#   USE_MEMFREE      1 (default) gate parallel starts on free RAM; 0 to disable
#
# Legacy: PARALLEL_JOBS sets PATH_JOBS if PATH_JOBS is unset.

# Use a project-local temp dir by default (/tmp is often on a full root filesystem).
pl_init_tmpdir() {
  local root="${1:-.}"
  PARALLEL_TMPDIR="${PARALLEL_TMPDIR:-$root/.parallel_tmp}"
  mkdir -p "$PARALLEL_TMPDIR"
  export TMPDIR="$PARALLEL_TMPDIR"
}

pl_mem_available_kb() {
  if [[ -r /proc/meminfo ]]; then
    awk '/^MemAvailable:/ { print $2; exit }' /proc/meminfo
    return
  fi
  if command -v vm_stat >/dev/null 2>&1; then
    local page_size pages_free
    page_size="$(getconf PAGESIZE 2>/dev/null || echo 4096)"
    pages_free="$(vm_stat | awk '/Pages free/ { gsub(/\./, "", $3); print $3 }')"
    echo $(( pages_free * page_size / 1024 ))
    return
  fi
  echo 0
}

pl_ncpu() {
  nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4
}

pl_init_dimacs_limits() {
  local mode="${1:-solve}"  # solve | batch

  SOLVER_MEM_MB="${SOLVER_MEM_MB:-2048}"
  EXPORT_MEM_MB="${EXPORT_MEM_MB:-8192}"
  MEM_RESERVE_MB="${MEM_RESERVE_MB:-4096}"
  USE_MEMFREE="${USE_MEMFREE:-1}"

  local mem_kb ncpu mem_mb budget batch_jobs path_jobs max_solvers

  mem_kb="$(pl_mem_available_kb)"
  ncpu="$(pl_ncpu)"
  mem_mb=$(( mem_kb / 1024 ))

  if [[ -n "${PARALLEL_JOBS:-}" && -z "${PATH_JOBS:-}" ]]; then
    PATH_JOBS="$PARALLEL_JOBS"
  fi

  batch_jobs="${BATCH_JOBS:-4}"
  if (( batch_jobs < 1 )); then
    batch_jobs=1
  fi

  budget=$(( mem_mb - MEM_RESERVE_MB ))
  if (( budget < SOLVER_MEM_MB )); then
    budget="$SOLVER_MEM_MB"
  fi

  max_solvers=$(( budget / SOLVER_MEM_MB ))
  if (( max_solvers < 1 )); then
    max_solvers=1
  fi

  if [[ -z "${PATH_JOBS:-}" ]]; then
    path_jobs=32
  else
    path_jobs="$PATH_JOBS"
  fi

  if (( path_jobs > max_solvers )); then
    path_jobs="$max_solvers"
  fi
  if (( path_jobs > ncpu )); then
    path_jobs="$ncpu"
  fi

  # When several configs run at once, split solver slots across them.
  if (( batch_jobs > 1 )); then
    local per_config=$(( max_solvers / batch_jobs ))
    if (( per_config < 1 )); then
      per_config=1
    fi
    if (( path_jobs > per_config )); then
      path_jobs="$per_config"
    fi
  fi

  if (( path_jobs < 1 )); then
    path_jobs=1
  fi

  BATCH_JOBS="$batch_jobs"
  PATH_JOBS="$path_jobs"
  export BATCH_JOBS PATH_JOBS SOLVER_MEM_MB EXPORT_MEM_MB MEM_RESERVE_MB USE_MEMFREE

  if [[ "$mode" == "batch" ]]; then
    local max_batch=$(( budget / EXPORT_MEM_MB ))
    if (( max_batch < 1 )); then
      max_batch=1
    fi
    if (( BATCH_JOBS > max_batch )); then
      BATCH_JOBS="$max_batch"
      export BATCH_JOBS
    fi
  fi
}

pl_memfree_arg() {
  local mb="$1"
  if [[ "${USE_MEMFREE:-1}" == "0" ]]; then
    return 0
  fi
  if (( mb > 0 )); then
    echo "--memfree ${mb}M"
  fi
}

pl_print_dimacs_limits() {
  local mode="${1:-solve}"
  local mem_kb mem_mb
  mem_kb="$(pl_mem_available_kb)"
  mem_mb=$(( mem_kb / 1024 ))

  echo "Memory safety:"
  echo "  MemAvailable:     ${mem_mb} MB  (reserve ${MEM_RESERVE_MB} MB)"
  echo "  BATCH_JOBS:       ${BATCH_JOBS} config(s) in parallel"
  echo "  PATH_JOBS:        ${PATH_JOBS} path solver(s) per config"
  echo "  SOLVER_MEM_MB:    ${SOLVER_MEM_MB} MB per solver (--memfree)"
  if [[ "$mode" == "batch" ]]; then
    echo "  EXPORT_MEM_MB:    ${EXPORT_MEM_MB} MB per export (--memfree)"
  fi
  echo "  Max solver slots: ~$(( (mem_mb - MEM_RESERVE_MB) / SOLVER_MEM_MB )) (shared if BATCH_JOBS>1)"
  echo "  Override: BATCH_JOBS=N PATH_JOBS=N SOLVER_MEM_MB=N bash scripts/..."
  echo ""
}
