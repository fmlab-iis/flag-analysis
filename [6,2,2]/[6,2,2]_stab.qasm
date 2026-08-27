OPENQASM 2.0;
include "qelib1.inc";
qreg q[6];
qreg ancZ[2];
qreg ancX[2];


cx  ancX[0], q[0];
cx q[0], ancZ[0];
cx ancX[1], q[2];
cx q[2], ancZ[0];
cx q[4], ancZ[0];

cx q[1], ancZ[1];


cx  ancX[0], q[1];

cx  ancX[0], q[2];

cx  ancX[0], q[3];


cx  ancX[1], q[3];
cx  ancX[1], q[4];

cx q[3], ancZ[1];
cx q[5], ancZ[1];

cx  ancX[1], q[5];
