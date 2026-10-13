# Load checkpoint @CHECKPOINT_NAME@ of trace @TRACE_UUID@ (entry point @ENTRY@), and simulate from it.
# Simulate one interval with e.g. SIM_FLAGS_EXTRA=-e100000000.
# Outputs: final-state.json

TRACE := @TRACE_DIR@
CHECKPOINT := @CHECKPOINT@
MEM := @MEM@
HARTS := @HARTS@

.PHONY: run
run:
	rm -f $(RUN)/final-state.json
	cd $(RUN) && $(SIM) -m$(MEM) -p$(HARTS) $(SIM_FLAGS_EXTRA) \
	    +dev-traffic-replay=$(TRACE) +load-checkpoint=$(CHECKPOINT) \
	    +final-state-dump=$(RUN)/final-state.json $(FESVR_FLAGS_EXTRA)
