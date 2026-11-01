"""Runtime settings from environment variables (never from the frontend)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("APP_ENV", "development")
    database_url: str = os.getenv("DATABASE_URL", "")            # empty -> in-memory stores (no persistence)
    redis_url: str = os.getenv("REDIS_URL", "")
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    jwt_ttl_minutes: int = int(os.getenv("JWT_TTL_MINUTES", "60"))
    football_provider: str = os.getenv("FOOTBALL_PROVIDER", "api_football")
    odds_provider: str = os.getenv("ODDS_PROVIDER", "api_football")   # api_football (by fixture id) | the_odds_api
    timezone: str = os.getenv("APP_TIMEZONE", "UTC")                    # defines "today" for the slate
    api_football_key: str = os.getenv("API_FOOTBALL_KEY", "")
    the_odds_api_key: str = os.getenv("THE_ODDS_API_KEY", "")
    football_data_token: str = os.getenv("FOOTBALL_DATA_TOKEN", "")
    cors_origins: list[str] = field(default_factory=lambda: _list("CORS_ORIGINS", "http://localhost:3000"))
    competitions: list[str] = field(default_factory=lambda: _list(
        "COMPETITION_WHITELIST",
        "epl,laliga,seriea,bundesliga,ligue1,eredivisie,primeira,ucl,uel,uecl,facup,eflcup,copadelrey,"
        "coppaitalia,dfbpokal,coupedefrance,knvbcup,tacadeportugal,uefasupercup,clubworldcup,championship,brasileirao"))
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))

    def provider_keys(self) -> dict[str, str]:
        return {"API_FOOTBALL_KEY": self.api_football_key, "THE_ODDS_API_KEY": self.the_odds_api_key,
                "FOOTBALL_DATA_TOKEN": self.football_data_token}

    def validate(self) -> None:
        if self.env == "production":
            missing = [n for n, v in (("JWT_SECRET", self.jwt_secret), ("DATABASE_URL", self.database_url)) if not v]
            if missing:
                raise RuntimeError(f"Missing required settings: {', '.join(missing)}")


settings = Settings()
