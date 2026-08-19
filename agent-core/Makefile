# Run the five processes locally, one per terminal.
#
# The long form still works and is what the README documents:
#   uv run app/runtimes/talent_screening/main.py
# These targets just save the typing and pin the two environment variables that
# this project always wants set.
#
#   make            list the targets
#   make screening  start one server (foreground, ctrl-c to stop)
#   make up         start all four servers in one terminal
#   make down       kill whatever is holding the ports

# qwen2.5:7b, not the llama3.2 default: this is tool-call heavy across five
# processes and the 3B model wanders. Override per run: make screening MODEL=llama3.2
MODEL ?= qwen2.5:7b

# This repo is often behind a TLS-inspecting proxy, which makes uv fail to fetch
# with `invalid peer certificate: UnknownIssuer`. Harmless when there is no proxy.
CERTS ?= 1

# Which requisition the supervisor works on: make supervisor JOB=J2002
JOB ?= J2001

RUN   := OLLAMA_MODEL=$(MODEL) UV_SYSTEM_CERTS=$(CERTS) uv run
UI    := ui

# 8123 is the local chat proxy, 5173 is vite. Both are in the list so `make down`
# stops the UI too — a stale :8123 holding an older stream_pipeline is the same
# class of bug as a stale agent on :9001.
PORTS := 8000 9001 9002 9007 8123 5173

# Your own tfvars. Never example.tfvars — the root .gitignore un-ignores that one,
# so a password in it would be committed.
TFVARS ?= dev.tfvars
TFDIR  := terraform/00_all_at_once

.DEFAULT_GOAL := help
.PHONY: help mcp screening outreach compliance supervisor up pipeline probe down ports \
        seed package artifacts deploy plan destroy check \
        ui-install ui-build ui-dev ui-api ui-clean test

help:
	@echo "Servers — one per terminal:"
	@echo "  make mcp          hr_skills_mcp        :8000/mcp   the scoring engine"
	@echo "  make screening    talent_screening     :9001       the only agent with tools"
	@echo "  make outreach     recruiting_outreach  :9002       no tools at all"
	@echo "  make compliance   people_compliance    :9007       reviews the draft"
	@echo ""
	@echo "Then, in one more terminal:"
	@echo "  make supervisor   run the pipeline        (JOB=$(JOB))"
	@echo "  make probe        one A2A round-trip, no model on the calling side"
	@echo "  make gateway      list/call the deployed Gateway's tools, SigV4-signed"
	@echo ""
	@echo "All at once:"
	@echo "  make up           start the four servers here, then drive them elsewhere"
	@echo "  make pipeline     start them, run $(JOB) end to end, exit"
	@echo ""
	@echo "The chat UI — three terminals: 'make up', then these two:"
	@echo "  make ui-api       the local chat proxy   :8123   no auth, no AWS"
	@echo "  make ui-dev       vite                   :5173   open this one"
	@echo ""
	@echo "  make test         all three suites (python + ui + proxy)"
	@echo ""
	@echo "  make ports        what is listening"
	@echo "  make down         kill it"
	@echo ""
	@echo "Deploy — the whole stack in one terraform apply:"
	@echo "  make artifacts    build the seven zips + the seed JSON + the UI"
	@echo "  make check        do the eight modules still chain? (terraform validate cannot tell you)"
	@echo "  make plan         artifacts, then terraform plan"
	@echo "  make deploy       artifacts, then terraform apply     (TFVARS=$(TFVARS))"
	@echo "  make destroy      tear it all down"
	@echo ""
	@echo "  MODEL=$(MODEL)  JOB=$(JOB)  TFVARS=$(TFVARS)"

# --- one server per target, foreground -------------------------------------

mcp:
	$(RUN) app/runtimes/hr_skills_mcp/main.py

screening:
	$(RUN) app/runtimes/talent_screening/main.py

outreach:
	$(RUN) app/runtimes/recruiting_outreach/main.py

compliance:
	$(RUN) app/runtimes/people_compliance/main.py

# --- the caller -------------------------------------------------------------

supervisor:
	$(RUN) app/runtimes/hiring_supervisor/main.py $(JOB)

# The protocol with no AI on the calling side. Reach for this first when a
# delegation misbehaves: whatever comes back came from the remote agent alone.
# PORT=9002 or 9007 to probe the others.
probe: PORT ?= 9001
probe:
	$(RUN) app/clients/raw_client.py $(PORT)

# The same idea one layer down, against the deployed Gateway. No agent, no
# bearer token: the Gateway runs authorizer_type = AWS_IAM, so this signs with
# your own credentials and needs bedrock-agentcore:InvokeGateway on them.
#
#   make gateway                                        # list the published tools
#   make gateway CALL=hrdata___get_requisition ARGS='{"job_id":"J2001"}'
gateway: CALL ?=
gateway: ARGS ?= {}
gateway:
	$(RUN) scripts/probe_gateway.py $(if $(CALL),--call $(CALL) --args '$(ARGS)')

# --- all four at once -------------------------------------------------------

up:
	$(RUN) scripts/run_local.py --job $(JOB)

pipeline:
	$(RUN) scripts/run_local.py --pipeline --job $(JOB)

# --- the chat UI, locally ----------------------------------------------------
#
# Three terminals: `make up` for the four agents, `make ui-api` for the proxy,
# `make ui-dev` for vite. Nothing here touches AWS — the local proxy accepts any
# login and the supervisor runs in its process against Ollama.

# A directory target, so npm install runs when package.json is newer than
# node_modules and is skipped every other time.
$(UI)/node_modules: $(UI)/package.json
	cd $(UI) && npm install
	@touch $@

ui-install: $(UI)/node_modules

ui-api:
	$(RUN) scripts/ui_server.py

ui-dev: $(UI)/node_modules
	cd $(UI) && npm run dev

# Develop the React against the DEPLOYED backend instead of the local one:
#   make ui-dev API_ORIGIN=https://d111111abcdef8.cloudfront.net

ui-build: $(UI)/node_modules
	cd $(UI) && npm run build

ui-clean:
	rm -rf $(UI)/node_modules $(UI)/dist $(UI)/proxy/node_modules

# --- tests ------------------------------------------------------------------
#
# Three suites, three runners, because they guard three different seams:
#   pytest      the agents, and the local proxy's wire format
#   ui          the SSE frame parser, against nasty chunk splits
#   ui/proxy    the two gateway event shapes
# None of them touch AWS.
test: $(UI)/node_modules $(UI)/proxy/node_modules
	$(RUN) pytest -q -W error::DeprecationWarning
	cd $(UI) && npm test
	cd $(UI)/proxy && npm test

$(UI)/proxy/node_modules: $(UI)/proxy/package.json
	cd $(UI)/proxy && npm install
	@touch $@

# --- deploy -----------------------------------------------------------------
#
# Nothing here has ever been applied to a real account. Treat the first `make
# deploy` as the real test, and see the README for the three things to check
# when it goes wrong.

seed:
	$(RUN) scripts/seed_s3.py

package:
	$(RUN) scripts/package.py

# terraform reads the zips, the JSON and every file in ui/dist at PLAN time —
# filemd5() is evaluated then, not at apply — so all three must exist before you
# go near terraform. A missing ui/dist fails with "call to function fileset
# failed", which does not sound like "you forgot to build the UI".
artifacts: package seed ui-build

# What `terraform validate` cannot answer: does every module's required input
# come from an earlier module's output, and do the runtime environment variables
# match the field names _shared/config.py reads?
check:
	$(RUN) scripts/check_terraform_chain.py

plan: artifacts
	cd $(TFDIR) && terraform init && terraform plan -var-file=$(TFVARS)

deploy: artifacts
	cd $(TFDIR) && terraform init && terraform apply -var-file=$(TFVARS)

destroy:
	cd $(TFDIR) && terraform destroy -var-file=$(TFVARS)

# --- ports ------------------------------------------------------------------

ports:
	@for p in $(PORTS); do \
	  pid=$$(lsof -nP -tiTCP:$$p -sTCP:LISTEN 2>/dev/null | head -1); \
	  if [ -n "$$pid" ]; then echo "  :$$p  busy (pid $$pid)"; else echo "  :$$p  free"; fi; \
	done

# Kills by port, not by process name. `pkill -f runtimes/...` misses servers
# started through `uv run`, which is how a stale agent carrying an older prompt
# ends up answering for a build you thought you had replaced.
down:
	@for p in $(PORTS); do \
	  pids=$$(lsof -nP -tiTCP:$$p -sTCP:LISTEN 2>/dev/null); \
	  if [ -n "$$pids" ]; then echo "  killing $$pids on :$$p"; kill -9 $$pids 2>/dev/null || true; fi; \
	done
	@echo "  stopped"
