# diagnostics_sensitivity.py
import json
from gik_icechain.risk.crma_model import CRMAModel, CRMAEvidence, EastAfricaCluster

def analyze_unit_sensitivity(json_path, target_units):
    d = json.load(open(json_path))
    thr = CRMAModel(cluster=EastAfricaCluster.HORN_ARID).evidence_thresholds(5)
    
    print(f"=== SENSITIVITY ANALYSIS FOR DATE: {d['date']} ===")
    
    for uid in target_units:
        u = d['units'].get(uid)
        if not u:
            print(f"Unit {uid} not found.")
            continue
            
        print(f"\n--- [Unit: {uid}] Current State: {u.get('risk_label')} (p_red: {u.get('p_red')}) ---")
        print(f"  Evidence active: exc72h={u.get('exceedance_72h')}, api={u.get('api_mm')}mm")
        
        # Mapping explicite des clusters selon la nomenclature réelle
        if "SOM" in uid:
            cluster = EastAfricaCluster.HORN_ARID
        elif "KEN" in uid:
            cluster = EastAfricaCluster.EQUATORIAL_EAST
        else:
            cluster = EastAfricaCluster.EQUATORIAL_EAST
            
        m = CRMAModel(cluster=cluster)
        m.build()
        
        base_ev = CRMAEvidence(
            exceedance_prob_24h=u.get('exceedance_24h', 0.0),
            exceedance_prob_72h=u.get('exceedance_72h', 0.0),
            exceedance_prob_7d=0.0,
            gpm_obs_24h=0.0,
            api_mm=u.get('api_mm', 0.0),
            spatial_coverage_fraction=u.get('spatial_coverage_fraction', 0.0),
            consecutive_signal_days=u.get('consecutive_signal_days', 0),
            sat_consecutive_days=u.get('sat_consecutive_days', 0),
            gpm_quality=2,
            gpm_missing=u.get('gpm_missing', True),
            rp_years=5,
            thresholds=thr
        )
        
        # Hypothèse A : Confiance Data
        ev_conf = base_ev.__dict__.copy()
        ev_conf['gpm_missing'] = False
        r_conf = m.infer(CRMAEvidence(**ev_conf))
        print(f"  [Hypothèse A] Si Data_Confidence -> High (GPM présent) :")
        print(f"    -> Label: {r_conf['risk_label']} | p_red: {r_conf['p_red']:.3f} | p_orange: {r_conf['p_orange']:.3f}")
        
        # Hypothèse B : Persistance / Mémoire Sols
        ev_soil = base_ev.__dict__.copy()
        ev_soil['sat_consecutive_days'] = 8  # Force Saturated_Long
        r_soil = m.infer(CRMAEvidence(**ev_soil))
        print(f"  [Hypothèse B] Si Soil_Memory -> Saturated_Long :")
        print(f"    -> Label: {r_soil['risk_label']} | p_red: {r_soil['p_red']:.3f} | p_orange: {r_soil['p_orange']:.3f}")
        
        # Hypothèse C : Multi-axes / Aléa Météo Forcé (Simule une forte exceedance sur Murang'a)
        ev_hazard = base_ev.__dict__.copy()
        ev_hazard['exceedance_prob_72h'] = 0.75  # On lui injecte l'aléa de la Somalie
        r_hazard = m.infer(CRMAEvidence(**ev_hazard))
        print(f"  [Hypothèse C] Si Aléa Météo Fort injecté (exc72h=0.75) :")
        print(f"    -> Label: {r_hazard['risk_label']} | p_red: {r_hazard['p_red']:.3f} | p_orange: {r_hazard['p_orange']:.3f}")

if __name__ == "__main__":
    analyze_unit_sensitivity('results/admin1_risk/2024-04-28_risk_scores.json', ['SOM_Sool', 'KEN_Murang\'a'])