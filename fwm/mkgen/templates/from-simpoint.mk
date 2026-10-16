# Load checkpoint @CHECKPOINT_NAME@ of trace @TRACE_UUID@ (entry point @ENTRY@), at SimPoint @SIMPOINT_INDEX@ of
# weight @SIMPOINT_WEIGHT@, and simulate its interval: INTERVAL instructions from the checkpoint (-e).
# spike exits 1 when -e stops it, so make reports "Error 1": that is how this run normally ends, and final-state.json
# then stays empty. Read the results from the simulator's own statistics.
# Outputs: final-state.json, written only if the program ends within the interval

TRACE := @TRACE_DIR@
CHECKPOINT := @CHECKPOINT@
MEM := @MEM@
HARTS := @HARTS@
INTERVAL := @INTERVAL@

.PHONY: run
run:
	rm -f $(RUN)/final-state.json
	cd $(RUN) && $(SIM) -m$(MEM) -p$(HARTS) -e$(INTERVAL) $(SIM_FLAGS_EXTRA) \
	    +dev-traffic-replay=$(TRACE) +load-checkpoint=$(CHECKPOINT) \
	    +final-state-dump=$(RUN)/final-state.json $(FESVR_FLAGS_EXTRA)
