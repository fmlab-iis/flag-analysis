OPENQASM 2.0;
include "qelib1.inc";

qreg q[7];
qreg ancX[0];
qreg ancZ[1];
qreg flagX[1];
qreg flagZ[0];


// =========================
// Stabilizer : (ZIZIZIZ)
// ancilla = ancZ[0] (Z basis), flag = flagX[0] (X basis)
// =========================
cx q[0], ancZ[0];
cx flagX[0], ancZ[0];
cx q[2], ancZ[0];
cx q[4], ancZ[0];
cx flagX[0], ancZ[0];
cx q[6], ancZ[0];

barrier;
