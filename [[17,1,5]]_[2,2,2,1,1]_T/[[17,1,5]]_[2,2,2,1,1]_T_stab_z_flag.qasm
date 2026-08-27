OPENQASM 2.0;
include "qelib1.inc";

qreg q[17];
qreg ancX[0];
qreg ancZ[8];
qreg flagX[8];
qreg flagZ[0];



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
cx q[5], ancZ[2];
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
barrier q, ancZ, flagX;

// --- Block 6 ---
cx q[14], ancZ[7];
cx flagX[0], ancZ[0];
cx flagX[1], ancZ[2];
cx flagX[2], ancZ[3];
barrier q, ancZ, flagX;

// --- Block 7 ---
cx q[5], ancZ[7];
cx flagX[0], ancZ[1];
cx flagX[1], ancZ[4];
cx flagX[2], ancZ[5];
cx flagX[5], ancZ[7]; 
barrier q, ancZ, flagX;

// --- Block 8 ---
cx q[1], ancZ[0];
cx q[5], ancZ[1];
cx q[9], ancZ[2];
cx q[10], ancZ[3];
cx q[13], ancZ[4];
cx q[14], ancZ[5];
cx q[11], ancZ[6];
cx q[2], ancZ[7];
barrier q, ancZ, flagX;

// --- Block 9 ---
cx flagX[6], ancZ[6];
cx q[6], ancZ[7];
barrier q, ancZ, flagX;

// --- Block 10 ---
cx q[7], ancZ[6];
cx flagX[3], ancZ[7];
barrier q, ancZ, flagX;

// --- Block 11 ---
cx q[16], ancZ[6];
cx q[3], ancZ[7];
barrier q, ancZ, flagX;

// --- Block 12 ---
cx flagX[6], ancZ[6];
cx flagX[5], ancZ[7];
barrier q, ancZ, flagX;

// --- Block 13 ---
cx q[15], ancZ[6];
cx flagX[4], ancZ[7];
barrier q, ancZ, flagX;

// --- Block 14 ---
cx q[10], ancZ[7];