OPENQASM 2.0;
include "qelib1.inc";

qreg q[5];          // data qubits
qreg ancX[4];       // ancillas measured in X (for X-type stabilizers)
qreg ancZ[0];       // ancillas measured in Z (for Z-type stabilizers)
qreg flagX[0];      // flags measured in X (paired with Z-type stabs)
qreg flagZ[4];      // flags measured in Z (paired with X-type stabs)





//barrier q, ancX, ancZ, flagX, flagZ;

// =========================
// Stabilizer 1 :  XZZXI
// ancilla = ancZ[0] (Z basis), flag = flagX[0] (X basis)
// =========================
cx  ancX[0] ,q[0];
cx ancX[0], flagZ[0];
cz q[1], ancX[0];
cz q[2], ancX[0];
cx ancX[0], flagZ[0];
cx ancX[0] , q[3];

// =========================
// Stabilizer 2 :  IXZZX
// ancilla = ancZ[0] (Z basis), flag = flagX[0] (X basis)
// =========================
cx  ancX[1] ,q[1];
cx ancX[1], flagZ[0];
cz q[2], ancX[1];
cz q[3], ancX[1];
cx ancX[1], flagZ[0];
cx ancX[1] , q[4];

// =========================
// Stabilizer 3 :  XIXZZ
// ancilla = ancZ[0] (Z basis), flag = flagX[0] (X basis)
// =========================
cx  ancX[2] ,q[0];
cx ancX[2], flagZ[0];
cz q[3], ancX[2];
cz q[4], ancX[2];
cx ancX[2], flagZ[0];
cx ancX[2] , q[2];


// =========================
// Stabilizer 4 :  XIXZZ
// ancilla = ancZ[0] (Z basis), flag = flagX[0] (X basis)
// =========================
cx  ancX[3] ,q[1];
cx ancX[3], flagZ[0];
cz q[0], ancX[3];
cz q[4], ancX[3];
cx ancX[3], flagZ[0];
cx ancX[3] , q[3];

