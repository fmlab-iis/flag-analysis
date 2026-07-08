OPENQASM 2.0;
include "qelib1.inc";
qreg q[5];
qreg ancX[4];
qreg ancZ[0];

cx ancX[0],q[0];
cz q[1],ancX[0];
cz q[2],ancX[0];
cx ancX[0],q[3];

// =========================
// Stabilizer 2 :  IXZZX
cx ancX[1],q[1];
cz q[2],ancX[1];
cz q[3],ancX[1];
cx ancX[1],q[4];

// =========================
// Stabilizer 3 :  XIXZZ

cx ancX[2],q[0];
cz q[3],ancX[2];
cz q[4],ancX[2];
cx ancX[2],q[2];

// =========================
// Stabilizer 4 :  XIXZZ
cx ancX[3],q[1];
cz q[0],ancX[3];
cz q[4],ancX[3];
cx ancX[3],q[3];