"""GitHub Advisory Database (GHSA) client via GraphQL API. Requires
GITHUB_TOKEN (already needed for repo access) — no additional auth needed.
Often includes the actual fix commit, which grounds Patch Forge's reasoning.
"""

from __future__ import annotations

import os
import requests
from typing import Any

GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
TIMEOUT = 30

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")


def query_ghsa_by_id(ghsa_id: str) -> dict[str, Any]:
    """Look up a GHSA ID (e.g., 'GHSA-8cf7-32gw-wr33') via GitHub GraphQL.

    Returns the advisory record including affected packages, severity, and fix references.
    """
    if not GITHUB_TOKEN:
        return {"error": "GITHUB_TOKEN not set", "advisory": None}

    query = """
    query($first: Int = 1, $id: String!) {
      securityAdvisories(first: $first, identifier: {ghsa: $id}) {
        edges {
          node {
            ghsaId
            summary
            description
            severity
            cvss {
              score
            }
            cwes(first: 10) {
              edges {
                node {
                  cweId
                }
              }
            }
            references {
              url
            }
            vulnerablePackages(first: 100, ecosystem: NPM) {
              edges {
                node {
                  package {
                    name
                  }
                  vulnerableVersionRange
                  firstPatchedVersion {
                    identifier
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    try:
        resp = requests.post(
            GRAPHQL_ENDPOINT,
            json={"query": query, "variables": {"id": ghsa_id}},
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            return {"error": data["errors"][0].get("message"), "advisory": None}
        edges = data.get("data", {}).get("securityAdvisories", {}).get("edges", [])
        return {"advisory": edges[0]["node"] if edges else None}
    except Exception as e:
        return {"error": str(e), "advisory": None}


def search_ghsa_by_package(package: str, ecosystem: str = "NPM") -> list[dict[str, Any]]:
    """Search GHSA for advisories affecting a specific package.

    Returns list of advisory records.
    """
    if not GITHUB_TOKEN:
        return []

    query = """
    query($first: Int = 10, $ecosystem: SecurityAdvisoryEcosystem!, $package: String!) {
      securityAdvisories(first: $first, ecosystem: $ecosystem) {
        edges {
          node {
            ghsaId
            summary
            severity
            vulnerablePackages(first: 100, ecosystem: $ecosystem) {
              edges {
                node {
                  package {
                    name
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    try:
        resp = requests.post(
            GRAPHQL_ENDPOINT,
            json={"query": query, "variables": {"ecosystem": ecosystem, "package": package}},
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        edges = data.get("data", {}).get("securityAdvisories", {}).get("edges", [])
        return [edge["node"] for edge in edges]
    except Exception:
        return []
