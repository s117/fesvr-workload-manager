# Run entry point @ENTRY@ in a fresh spawn of sysroot @SYSROOT_NAME@ (a copy, or links to the repository's files, as
# the entry point's copy_spawn says): the program does its real system calls on this host (Linux only), inside it.
# Outputs: final-state.json, console.stdout, console.stderr (the console is also printed), and the post-run sysroot/,
# with the files the program wrote.

FWM ?= @FWM@
REPO := @REPO@
ENTRY := @ENTRY@
SYSROOT := $(RUN)/sysroot

MEM := @MEM@
NPROCS := @NPROCS@
CWD := @CWD@
BOOT_ELF := @BOOT_ELF@
APP_CMD := @APP_CMD@
STDIN_FILE := @STDIN_FILE@

BOOT_ELF_FLAGS_EXTRA ?=
APP_CMD_EXTRA ?=

.PHONY: run
run:
	rm -rf $(RUN)/final-state.json $(RUN)/console.stdout $(RUN)/console.stderr
	$(FWM) --repo-path $(REPO) spawn --force $(ENTRY) $(SYSROOT)
	cd $(RUN) && $(SIM) -m$(MEM) -p$(NPROCS) $(SIM_FLAGS_EXTRA) \
	    +chroot=$(SYSROOT) +target-cwd=$(CWD) \
	    +final-state-dump=$(RUN)/final-state.json +std-dump=$(RUN)/console $(FESVR_FLAGS_EXTRA) \
	    $(BOOT_ELF) $(BOOT_ELF_FLAGS_EXTRA) $(APP_CMD) $(APP_CMD_EXTRA) $(if $(STDIN_FILE),< $(SYSROOT)$(STDIN_FILE))
