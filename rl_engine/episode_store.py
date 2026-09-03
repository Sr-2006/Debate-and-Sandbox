import os
import json
import sqlite3
from typing import Dict, Any, Optional, List
from rl_engine.config import RL_OUTBOX_DIR
from rl_engine.contracts import RLAdvisoryData, LearningEpisodeData
from Arse_shadow.shadow_sandbox.persistence import get_db_connection


class EpisodeStore:
    """Persists RL Advisories and Learning Episodes to SQLite and outbox directory."""

    def __init__(self, outbox_dir: str = RL_OUTBOX_DIR):
        self.outbox_dir = outbox_dir
        os.makedirs(self.outbox_dir, exist_ok=True)

    def save_advisory(self, advisory: RLAdvisoryData, db_path: Optional[str] = None) -> bool:
        """Inserts RLAdvisory into rl_advisories database table."""
        conn = get_db_connection(db_path)
        adv_dict = advisory.to_dict() if hasattr(advisory, "to_dict") else advisory
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO rl_advisories (
                        advisory_id, incident_id, run_id, payload_hash, policy_version,
                        model_version, operating_mode, recommendation, scores_json, uncertainty,
                        sample_size, cold_start, influence_allowed, feature_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        adv_dict["advisory_id"],
                        adv_dict["incident_id"],
                        adv_dict["run_id"],
                        adv_dict.get("payload_hash", "hash_unknown"),
                        adv_dict["policy"]["policy_version"],
                        adv_dict["policy"]["model_version"],
                        adv_dict["policy"]["operating_mode"],
                        adv_dict["recommendation"],
                        json.dumps(adv_dict["action_scores"]),
                        float(adv_dict["uncertainty"]),
                        int(adv_dict["sample_size"]),
                        1 if adv_dict["cold_start"] else 0,
                        1 if adv_dict["influence_allowed"] else 0,
                        adv_dict["feature_hash"],
                        adv_dict["created_at"]
                    )
                )
            return True
        except sqlite3.IntegrityError:
            return False  # Idempotent duplicate skip

    def save_episode(self, episode: LearningEpisodeData, db_path: Optional[str] = None) -> bool:
        """Inserts LearningEpisode into learning_episodes table and writes outbox JSON file."""
        conn = get_db_connection(db_path)
        ep_dict = episode.to_dict() if hasattr(episode, "to_dict") else episode
        
        # 1. Database insert (idempotent)
        saved_db = False
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO learning_episodes (
                        episode_id, incident_id, run_id, payload_hash, capability,
                        target_kind, feature_schema_version, features_json, feature_vector_json,
                        feature_hash, advisory_action, behavior_action, phase4_status,
                        simulated, eligible, eligibility_reason, reward, sample_weight, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        ep_dict["episode_id"],
                        ep_dict["incident_id"],
                        ep_dict["run_id"],
                        ep_dict["payload_hash"],
                        ep_dict["proposal"]["intent_type"],
                        ep_dict["proposal"]["target_kind"],
                        ep_dict["context"]["feature_schema_version"],
                        json.dumps(ep_dict["context"]["features"]),
                        json.dumps(ep_dict["context"]["feature_vector"]),
                        ep_dict["context"]["feature_hash"],
                        ep_dict["advisory"].get("recommendation", "ABSTAIN"),
                        ep_dict["learning"]["behavior_action"],
                        ep_dict["phase4"]["status"],
                        1 if ep_dict["phase4"]["simulated"] else 0,
                        1 if ep_dict["learning"]["eligible"] else 0,
                        ep_dict["learning"]["eligibility_reason"],
                        ep_dict["learning"]["reward"],
                        float(ep_dict["learning"]["sample_weight"]),
                        ep_dict["created_at"]
                    )
                )
            saved_db = True
        except sqlite3.IntegrityError:
            saved_db = False

        # 2. Outbox JSON file write
        try:
            filename = f"{ep_dict['episode_id']}.learning_episode.json"
            filepath = os.path.join(self.outbox_dir, filename)
            tmppath = filepath + ".tmp"
            with open(tmppath, "w", encoding="utf-8") as f:
                json.dump(ep_dict, f, indent=2)
            os.replace(tmppath, filepath)
        except Exception:
            pass

        return saved_db

    def get_eligible_episodes(self, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
        """Queries eligible real execution episodes from database for LinUCB training."""
        conn = get_db_connection(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT episode_id, behavior_action, feature_vector_json, reward, sample_weight, created_at
            FROM learning_episodes
            WHERE eligible = 1 AND reward IS NOT NULL
            ORDER BY created_at ASC;
            """
        )
        rows = cursor.fetchall()
        episodes = []
        for r in rows:
            episodes.append({
                "episode_id": r[0],
                "action": r[1],
                "feature_vector": json.loads(r[2]),
                "reward": float(r[3]),
                "sample_weight": float(r[4]),
                "created_at": r[5]
            })
        return episodes
