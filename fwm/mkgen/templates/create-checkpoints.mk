# Create the checkpoints listed in ckpt.desc by replaying trace @TRACE_UUID@ of entry point @ENTRY@.
# Outputs: checkpoints/ (only the checkpoint files), final-state.json

TRACE := @TRACE_DIR@
MEM := @MEM@
HARTS := @HARTS@
CHECKPOINT_DESC := $(RUN)/ckpt.desc

.PHONY: run
run:
	rm -rf $(RUN)/final-state.json $(RUN)/checkpoints
	mkdir $(RUN)/checkpoints
	cd $(RUN)/checkpoints && $(SIM) -m$(MEM) -p$(HARTS) $(SIM_FLAGS_EXTRA) \
	    +dev-traffic-replay=$(TRACE) +create-checkpoint=$(CHECKPOINT_DESC) \
	    +final-state-dump=$(RUN)/final-state.json $(FESVR_FLAGS_EXTRA)
