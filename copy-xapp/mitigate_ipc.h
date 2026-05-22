#ifndef MITIGATE_IPC_H
#define MITIGATE_IPC_H

/* Path of the UNIX domain socket the mitigation xApp listens on. */
#define MITIGATE_SOCK_PATH      "/tmp/sec_xapp_mitigate.sock"

/* How long moni waits for an ACK before giving up (milliseconds). */
#define MITIGATE_ACK_TIMEOUT_MS 500

/* Maximum JSON message length (bytes) including newline. */
#define MITIGATE_MAX_MSG_LEN    1024

/* moni waits this long at severity==0 before sending RESTORE (milliseconds). */
#define RESTORE_GRACE_MS        10000

#endif /* MITIGATE_IPC_H */
