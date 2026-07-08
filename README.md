# SAT‑based Flag Qubit Circuit Verification Tool

A tool to verify flag‑based stabilizer extraction circuits using SAT checks.

---

## Installation

Clone the repository and install requirements:

```bash
git clone https://github.com/boriswu412/flag-analysis.git
cd flag-analysis
pip install -r requirements.txt
```

(Optional) create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
```

> Requires Python 3.9+ (tested with 3.10).

---

## Usage

Edit `config.txt` with two rows:
```
qasm_path=path/to/circuit.qasm
stab_txt_path=path/to/stab.txt
```

Then run:
```bash
python flag_analysis.py
```

### QASM file
**Qubits:**
- `q` — data qubits
- `ancX`, `ancZ` — ancilla qubits in X or Z basis
- `flagX`, `flagZ` — flag qubits in X or Z basis

**Gates:**
- Supported: `cx` (CNOT), `cz`
- Hadamard gates on ancillas or flags are replaced by basis changes (X ↔ Z)
- Measurements are omitted from QASM
- Reused qubits in flag circuits are treated as distinct  qubits

### `stab.txt`
Defines the parity‑check matrix (first X, then Z rows). Row order must match ancilla order.

---

## Running and Output

The verification includes **four steps**:

1. **Syndrome correctness:** verify when no faults in the flag circuit the circuit is a syndrome extraction circuit.
2. **Fault detection:** find gates that cause high‑weight errors → stored in `bad_location`.
3. **Flag check:** verify when faults on `bad_locations` cause a high weight error will trigger at least one flag.
4. **Syndrome uniqueness:** check after a round of flag circuit and a round of no flag circuit the generalized syndrome is unique up to degenracy.

Each step should output **“Success.”**

If Step 3 or 4 fails, a counterexample is printed showing which variables are `True`.

Example (Step 3):
```
faulty_gate4_z1 = True
faulty_gate4_x0 = True
```
Gate 4 (`cx [2,7]`): fault with X on 2 and Z on 7 causes a high‑weight error but no flag.

Example (Step 4):
```
faulty_gate19_z1_p2 = True
faulty_gate14_z1_p1 = True
faulty_gate19_z0_p2 = True
faulty_gate19_x0_p2 = True
faulty_gate14_x0_p1 = True
```
Gates 19 (`cx [8,1]`) YZ error  and 14 (`cx [7,2]`) XZ error: these faults yield identical generalized syndromes, violating uniqueness.

---

## Server: proof_protocol

Run multi-path protocol verification on a Linux server without Jupyter.

### One-time setup (Ubuntu/Debian)

```bash
bash scripts/setup_server.sh
source venv/bin/activate
```

This installs Python dependencies from `requirements.txt` and system SAT solvers (`minisat` by default; optional `cryptominisat` for `cryptominisat5`). Override with `DIMACS_SOLVER_BIN=cryptominisat5` if needed.

### Run verification

```bash
python run_proof_protocol.py \
  --protocol ./protocols/d_3_lai_protocol.json \
  --config   ./[[5,1,3]]_[2,2]_T_fix/[[5,1,3]]_[2,2]_T_fix_lai_3_protocol_config.txt \
  --t 1
```

**Exit codes:**
- `0` — all verified paths are UNSAT (proof holds)
- `1` — at least one path is SAT (counterexample) or a runtime error occurred

**Metrics:** written to `{config_stem}_proof_metrics.txt` next to the config file (e.g. `[[5,1,3]]_[2,2]_T_fix_lai_3_protocol_config_proof_metrics.txt`).

### Flag-raised batch (Step 3)

Verify that high-weight errors at any gate position raise at least one flag, for every `flag_syndrome` and `*_flag` QASM in each `jobs.txt` config (default: all gates may fault, not only `find_bad_locations`):

```bash
PARALLEL_JOBS=2 PARALLEL_TMPDIR=$HOME/tmp bash scripts/run_batch_flag.sh
```

**Parameters:**
- `t` (jobs.txt column 3, or `--t`): used when column 4 / `--w` omitted
- `w` (jobs.txt column 4, `--w`, or `flag_w=` in config): **max fault sites** for the flag check (`AtMost`). Default: `t`
- Flag must raise when stabilizer error **weight > w** (checked as `PbGe(..., w+1)`)

Example `jobs.txt` line: `protocol.json\tconfig.txt\t1\t1` (d=3, one fault site, weight > 1)

**Metrics:** `results_txt/{config_stem}_flag_raised_metrics.txt` — `Failed circuits: X/Y` must be `0/Y` to pass.

Regenerate the paper table (same 18-column layout as control-flow / FTEC):

```bash
python scripts/generate_flag_metrics_table.py
```

Output: `results_txt/flag_raised_metrics_table.tex`
