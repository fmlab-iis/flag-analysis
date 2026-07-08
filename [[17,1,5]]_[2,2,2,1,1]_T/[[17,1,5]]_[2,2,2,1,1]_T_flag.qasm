OPENQASM 2.0;
include "qelib1.inc";

qreg q[17];          // data qubits
qreg ancX[8];        // ancillas measured in X (for X-type stabilizers)
qreg ancZ[8];        // ancillas measured in Z (for Z-type stabilizers)
qreg flagX[7];       // flags measured in X (paired with Z-type stabs)
qreg flagZ[7];       // flags measured in Z (paired with X-type stabs)

// ========== Z-type stabilizer extraction ==========

// --- Block 1 ---
cx q[2], ancZ[0];
cx q[0], ancZ[1];
cx q[4], ancZ[2];
cx q[6], ancZ[3];
cx q[8], ancZ[4];
cx q[10], ancZ[5];
cx q[13], ancZ[7];
barrier q, ancZ, flagX;

// --- Block 2 ---
cx flagX[0], ancZ[0];
cx flagX[1], ancZ[2];
cx flagX[2], ancZ[3];
cx flagX[3], ancZ[7];
barrier q, ancZ, flagX;

// --- Block 3 ---
cx flagX[0], ancZ[1];
cx flagX[1], ancZ[4];
cx flagX[2], ancZ[5];
cx q[9], ancZ[7];
barrier q, ancZ, flagX;

// --- Block 4 ---
cx q[0], ancZ[0];
cx q[2], ancZ[1];
cx q[5] , ancZ[2];
cx q[7], ancZ[3];
cx q[9], ancZ[4];
cx q[11], ancZ[5];

cx flagX[4], ancZ[7];
barrier q, ancZ, flagX;

// --- Block 5 ---
cx q[3], ancZ[0];
cx q[4], ancZ[1];
cx q[8], ancZ[2];
cx q[11], ancZ[3];
cx q[12], ancZ[4];
cx q[15], ancZ[5];
cx q[14], ancZ[7];
cx flagX[0], ancZ[0];
cx flagX[1], ancZ[2];
cx flagX[2], ancZ[3];
barrier q, ancZ, flagX;

// --- Block 6 ---
cx q[5], ancZ[7];
cx flagX[0], ancZ[1];
cx flagX[1], ancZ[4];
cx flagX[2], ancZ[5];

barrier q, ancZ, flagX;

// --- Block 7 ---
cx q[1], ancZ[0];
cx q[5], ancZ[1];
cx q[9], ancZ[2];
cx q[10], ancZ[3];
cx q[13], ancZ[4];
cx q[14], ancZ[5];
cx q[11], ancZ[6];
cx q[2], ancZ[7];
barrier q, ancZ, flagX;

// --- Block 8 ---
cx flagX[5], ancZ[6];
cx q[6], ancZ[7];
barrier q, ancZ, flagX;

// --- Block 9 ---
cx q[7], ancZ[6];
cx flagX[3], ancZ[7];
barrier q, ancZ, flagX;

// --- Block 10 ---
cx q[16], ancZ[6];
cx q[3], ancZ[7];
barrier q, ancZ, flagX;

// --- Block 11 ---
cx flagX[6], ancZ[6];
cx flagX[5], ancZ[7];
barrier q, ancZ, flagX;

// --- Block 12 ---
cx q[15], ancZ[6];
cx flagX[4], ancZ[7];
barrier q, ancZ, flagX;

// --- Block 13 ---
cx q[10], ancZ[7];


// ========== X-type stabilizer extraction ==========

// --- Block 1 ---
cx ancX[0], q[2];
cx ancX[1], q[0];
cx ancX[2], q[4];
cx ancX[3], q[6];
cx ancX[4], q[8];
cx ancX[5], q[10];
cx ancX[7], q[13];
barrier q, ancX, flagZ;

// --- Block 2 ---
cx ancX[0], flagZ[0];
cx ancX[2], flagZ[1];
cx ancX[3], flagZ[2];
cx ancX[7], flagZ[3];
barrier q, ancX, flagZ;

// --- Block 3 ---
cx ancX[1], flagZ[0];
cx ancX[4], flagZ[1];
cx ancX[5], flagZ[2];
cx ancX[7], q[9];
barrier q, ancX, flagZ;

// --- Block 4 ---
cx ancX[0], q[0];
cx ancX[1], q[2];
cx ancX[2], q[5];
cx ancX[3], q[7];
cx ancX[4], q[9];
cx ancX[5], q[11];
cx ancX[7], flagZ[4];
barrier q, ancX, flagZ;

// --- Block 5 ---
cx ancX[0], q[3];
cx ancX[1], q[4];
cx ancX[2], q[8];
cx ancX[3], q[11];
cx ancX[4], q[12];
cx ancX[5], q[15];
cx ancX[7], q[14];
cx ancX[0], flagZ[0];
cx ancX[2], flagZ[1];
cx ancX[3], flagZ[2];
barrier q, ancX, flagZ;

// --- Block 6 ---
cx ancX[7], q[5];
cx ancX[1], flagZ[0];
cx ancX[4], flagZ[1];
cx ancX[5], flagZ[2];

barrier q, ancX, flagZ;

// --- Block 7 ---
cx ancX[0], q[1];
cx ancX[1], q[5];
cx ancX[2], q[9];
cx ancX[3], q[10];
cx ancX[4], q[13];
cx ancX[5], q[14];
cx ancX[6], q[11];
cx ancX[7], q[2];
barrier q, ancX, flagZ;

// --- Block 8 ---
cx ancX[6], flagZ[5];
cx ancX[7], q[6];
barrier q, ancX, flagZ;

// --- Block 9 ---
cx ancX[6], q[7];
cx ancX[7], flagZ[3];
barrier q, ancX, flagZ;

// --- Block 10 ---
cx ancX[6], q[16];
cx ancX[7], q[3];
barrier q, ancX, flagZ;

// --- Block 11 ---
cx ancX[6], flagZ[6];
cx ancX[7], flagZ[5];
barrier q, ancX, flagZ;

// --- Block 12 ---
cx ancX[6], q[15];
cx ancX[7], flagZ[4];
barrier q, ancX, flagZ;

// --- Block 13 ---
cx ancX[7], q[10];
