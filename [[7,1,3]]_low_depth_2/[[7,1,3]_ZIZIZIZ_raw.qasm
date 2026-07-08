OPENQASM 2.0;
include "qelib1.inc";

qreg q[7];
qreg ancZ[1];

// Stabilizer : (ZIZIZIZ) — raw (no flags)
cx q[0], ancZ[0];
cx q[2], ancZ[0];
cx q[4], ancZ[0];
cx q[6], ancZ[0];

barrier;
