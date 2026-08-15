# Chart render QA (Phase 6B)

Automated checks performed before each chart save:
- figure bounding-box clipping for titles, legends, axis labels, notes, and annotations
- annotation overlap within designated notes panels (where applicable)
- minimum resolution 1600×900 at 100 DPI

## 01_opportunity_priority
- Dimensions: 1600×900
- Title present: yes
- Legend position: 4
- Missing-data treatment: minimum bubble size for unavailable/low observed volume
- Clipping check: pass
- Overlap check: pass
- Overall: pass

## 02_strength_demand_heatmap
- Dimensions: 1600×900
- Title present: yes
- Legend position: 4
- Missing-data treatment: grey hatched placeholder with Volume unavailable label
- Clipping check: pass
- Overlap check: pass
- Overall: pass

## 03_supplier_concentration
- Dimensions: 1600×900
- Title present: yes
- Legend position: 9
- Missing-data treatment: grey hatched bars for volume unavailable molecules
- Clipping check: pass
- Overlap check: pass
- Overall: pass

## 04_pricing_scenarios
- Dimensions: 1600×900
- Title present: yes
- Legend position: 2
- Missing-data treatment: side panel lists unavailable molecules with full reasons
- Clipping check: pass
- Overlap check: pass
- Overall: pass

## 05_tender_readiness
- Dimensions: 1600×900
- Title present: yes
- Legend position: none
- Missing-data treatment: canonical matrix drives both imshow colours and cell labels
- Clipping check: pass
- Overlap check: pass
- Overall: pass
