"""GET /api/twin/eis -- run a full EIS biosensor assay on the digital twin's
graphene-PBASE-aptamer electrode model (baseline -> exposure -> binding ->
second measurement -> compare -> result). Public and read-only like
GET /devices: nothing here is per-user and no state is stored."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from digital_twin.bandage.impedance import EisAssay

router = APIRouter(prefix="/api/twin", tags=["twin-eis"])

_assay = EisAssay()  # frozen inputs, stateless: safe to share


@router.get("/eis")
def get_eis_assay(
    cfu: float = Query(0.0, ge=0.0, le=1e9, description="E. coli concentration, CFU/mL (0 = clean control)"),
    seed: int = Query(0, ge=0, le=2**63 - 1, description="measurement-noise seed"),
    noise_pct: float = Query(1.0, ge=0.0, le=50.0, description="per-point noise, % of |Z|"),
    incubation_s: Optional[float] = Query(
        None, ge=0.0, le=1e6, description="binding contact time in s; omit for equilibrium"
    ),
) -> dict:
    return _assay.run(cfu, seed=seed, noise_pct=noise_pct, incubation_s=incubation_s).to_dict()
