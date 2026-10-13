# Replay trace @TRACE_UUID@ of entry point @ENTRY@. A replay runs no system call on the host: the program prints
# nothing and writes no file.
# Outputs: final-state.json

TRACE := @TRACE_DIR@
MEM := @MEM@
HARTS := @HARTS@

.PHONY: run
run:
	rm -f $(RUN)/final-state.json
	cd $(RUN) && $(SIM) -m$(MEM) -p$(HARTS) $(SIM_FLAGS_EXTRA) \
	    +dev-traffic-replay=$(TRACE) +final-state-dump=$(RUN)/final-state.json $(FESVR_FLAGS_EXTRA)
