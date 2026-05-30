from protected.schema import Claim, ExtractionResult
from extractor.provider import LlamaCppProvider
import json
from pathlib import Path

_provider = LlamaCppProvider()


async def extract(abstract_id: str, abstract_text: str) -> ExtractionResult:
    prompts_dir = Path(__file__).parent.parent / "prompts"
    system_prompt = (prompts_dir / "system_prompt.md").read_text()
    examples = (prompts_dir / "examples.md").read_text().strip()
    if examples:
        system_prompt = system_prompt + "\n\n" + examples
    raw = _provider.complete(system_prompt, abstract_text)
    data = json.loads(raw)
    claims = [Claim(claim_text=c) for c in data.get("claims", [])]
    return ExtractionResult(abstract_id=abstract_id, claims=claims)
