OPENQASM 2.0;
include "qelib1.inc";

qreg q[17];
qreg ancX[8];
qreg ancZ[0];
qreg flagX[0];
qreg flagZ[8];


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
barrier q, ancX, flagZ;

// --- Block 6 ---
cx ancX[7], q[14];
cx ancX[0], flagZ[0];
cx ancX[2], flagZ[1];
cx ancX[3], flagZ[2];
barrier q, ancX, flagZ;

// --- Block 7 ---
cx ancX[7], q[5];
cx ancX[1], flagZ[0];
cx ancX[4], flagZ[1];
cx ancX[5], flagZ[2];
cx ancX[7], flagZ[5];
barrier q, ancX, flagZ;

// --- Block 8 ---
cx ancX[0], q[1];
cx ancX[1], q[5];
cx ancX[2], q[9];
cx ancX[3], q[10];
cx ancX[4], q[13];
cx ancX[5], q[14];
cx ancX[6], q[11];
cx ancX[7], q[2];
barrier q, ancX, flagZ;

// --- Block 9 ---
cx ancX[6], flagZ[6];
cx ancX[7], q[6];
barrier q, ancX, flagZ;

// --- Block 10 ---
cx ancX[6], q[7];
cx ancX[7], flagZ[3];
barrier q, ancX, flagZ;

// --- Block 11 ---
cx ancX[6], q[16];
cx ancX[7], q[3];
barrier q, ancX, flagZ;

// --- Block 12 ---
cx ancX[6], flagZ[6];
cx ancX[7], flagZ[5];
barrier q, ancX, flagZ;

// --- Block 13 ---
cx ancX[6], q[15];
cx ancX[7], flagZ[4];
barrier q, ancX, flagZ;

// --- Block 14 ---
cx ancX[7], q[10];