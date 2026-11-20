"""AutoPatch Core: AutoPatch's own bug detection and repair engine.

It needs no API key and sends nothing off the machine. It combines:
  facts.py     - static facts about each Python function (no code runs)
  detectors.py - bug detectors: taint tracking into dangerous calls, crash patterns, intent mismatches
  probe.py     - a fuzzer/prober that runs functions in the sandbox to confirm each bug
  codegen.py   - test generation and repair (fix templates plus a search over small mutations)
  engine.py    - the engine the pipeline calls (scan, analyze, write_tests, write_fix)
"""
