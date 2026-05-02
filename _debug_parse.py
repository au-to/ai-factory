from pathlib import Path
import json, traceback
from factory.bus.store import ContextBus

bus = ContextBus(Path("workspace"))
with open("workspace/artifacts/93f11b1c/tech-spec.json") as f:
    data = json.load(f)

print(f"Keys in file: {list(data.keys())}")

from factory.bus.schemas import TechSpecArtifact
print(f"Schema fields: {list(TechSpecArtifact.model_fields.keys())}")

print()
try:
    version = bus.write_raw("design", data, "93f11b1c")
    print(f"Success! Version: {version}")
except Exception as e:
    print(f"Error: {e}")
    traceback.print_exc()
