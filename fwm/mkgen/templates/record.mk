# Record a run of entry point @ENTRY@ in a fresh copy of sysroot @SYSROOT_NAME@.
# Outputs: trace/ (the recording), final-state.json, console.stdout, console.stderr, and the post-run sysroot/.

FWM ?= @FWM@
REPO := @REPO@
SYSROOT_NAME := @SYSROOT_NAME@
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
	rm -rf $(RUN)/trace $(RUN)/final-state.json $(RUN)/console.stdout $(RUN)/console.stderr
	$(FWM) --repo-path $(REPO) spawn --force $(SYSROOT_NAME) $(SYSROOT)
	mkdir $(RUN)/trace
	cd $(RUN) && $(SIM) -m$(MEM) -p$(NPROCS) $(SIM_FLAGS_EXTRA) \
	    +chroot=$(SYSROOT) +target-cwd=$(CWD) +dev-traffic-record=$(RUN)/trace \
	    +final-state-dump=$(RUN)/final-state.json +std-dump=$(RUN)/console $(FESVR_FLAGS_EXTRA) \
	    $(BOOT_ELF) $(BOOT_ELF_FLAGS_EXTRA) $(APP_CMD) $(APP_CMD_EXTRA) $(if $(STDIN_FILE),< $(SYSROOT)$(STDIN_FILE))
