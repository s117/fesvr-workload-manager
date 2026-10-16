# Load checkpoint @CHECKPOINT_NAME@ of trace @TRACE_UUID@ (entry point @ENTRY@), and simulate from it to the end of
# the program, or for n instructions with SIM_FLAGS_EXTRA=-e<n>. (`fwm mkgen from-simpoint` simulates a SimPoint
# checkpoint's interval.)
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
