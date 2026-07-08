OPENQASM 2.0;
include "qelib1.inc";

qreg q[7];
qreg ancZ[1];

// Stabilizer : (IZZIIZZ) — raw (no flags)
cx q[1], ancZ[0];
cx q[2], ancZ[0];
cx q[5], ancZ[0];
cx q[6], ancZ[0];

barrier;
