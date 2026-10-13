import os
import re
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langchain_neo4j import Neo4jGraph

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

graph = Neo4jGraph(
    url=NEO4J_URI,
    username=NEO4J_USERNAME,
    password=NEO4J_PASSWORD,
    database=NEO4J_DATABASE,
)

BASE_URL = "https://www.tn.gov.in/"
LIST_URL = "https://www.tn.gov.in/scheme_list.php?dep_id=Mg=="


def setup_schema_indexes():
  """Create unique constraints to optimize search and maintain data integrity."""
  constraints = [
      "CREATE CONSTRAINT FOR (d:Department) REQUIRE d.dep_id IS UNIQUE;",
      "CREATE CONSTRAINT FOR (s:Scheme) REQUIRE s.url IS UNIQUE;",
      "CREATE CONSTRAINT FOR (e:Eligibility) REQUIRE e.description IS UNIQUE;",
      "CREATE CONSTRAINT FOR (b:Benefit) REQUIRE b.description IS UNIQUE;",
      "CREATE CONSTRAINT FOR (a:Agency) REQUIRE a.name IS UNIQUE;",
  ]
  for stmt in constraints:
    try:
      graph.query(stmt)
    except Exception:
      pass  # Ignore if constraints already exist


def extract_key_sections(soup):
  """Parse tables or structured list elements to extract sub-entities."""
  details_dict = {
      "eligibility": [],
      "benefits": [],
      "documents": [],
      "agency": "Government of Tamil Nadu",
      "full_text": "",
  }

  tables = soup.find_all("table")
  if tables:
    for table in tables:
      rows = table.find_all("tr")
      for row in rows:
        cols = row.find_all(["th", "td"])
        if len(cols) >= 2:
          label = cols[0].get_text(strip=True).lower()
          val = cols[1].get_text(strip=True)

          if "eligibil" in label or "who can" in label:
            details_dict["eligibility"].append(val)
          elif "benefit" in label or "assistance" in label or "grant" in label:
            details_dict["benefits"].append(val)
          elif "document" in label or "proof" in label:
            details_dict["documents"].append(val)
          elif "agency" in label or "sponsored" in label or "funded" in label:
            details_dict["agency"] = val

  # Fallback parsing using keyword regular expressions if tables are missing
  full_text = soup.get_text(separator=" ", strip=True)
  details_dict["full_text"] = full_text

  if not details_dict["eligibility"]:
    matches = re.findall(
        r"(?:Eligibility|Eligible Farmers?):?\s*([^.\n]+)",
        full_text,
        re.IGNORECASE,
    )
    details_dict["eligibility"] = [m.strip() for m in matches if len(m) > 5]

  if not details_dict["benefits"]:
    matches = re.findall(
        r"(?:Benefits?|Pattern of Assistance):?\s*([^.\n]+)",
        full_text,
        re.IGNORECASE,
    )
    details_dict["benefits"] = [m.strip() for m in matches if len(m) > 5]

  return details_dict


def scrape_and_ingest():
  setup_schema_indexes()

  print("Fetching main agriculture scheme list...")
  response = requests.get(LIST_URL, timeout=15)
  if response.status_code != 200:
    print(f"Failed to fetch scheme list. Status code: {response.status_code}")
    return

  soup = BeautifulSoup(response.text, "html.parser")

  # 1. Create Department Node
  graph.query("""
    MERGE (d:Department {name: 'Agriculture and Farmers Welfare'})
    SET d.dep_id = 'Mg=='
    RETURN d
    """)

  links = soup.find_all("a", href=True)
  scheme_links = list(
      set([
          link["href"]
          for link in links
          if "scheme_details.php" in link["href"]
      ])
  )

  print(
      f"Found {len(scheme_links)} detail links. Ingesting graph with multi-node"
      " relationships..."
  )

  for link in scheme_links:
    detail_url = (
        link if link.startswith("http") else BASE_URL + link.lstrip("/")
    )
    try:
      res = requests.get(detail_url, timeout=10)
      if res.status_code != 200:
        continue

      detail_soup = BeautifulSoup(res.text, "html.parser")

      title_elem = detail_soup.find(["h1", "h2", "h3", "strong", "b"])
      title = (
          title_elem.get_text(strip=True)
          if title_elem
          else "Agricultural Scheme"
      )

      extracted = extract_key_sections(detail_soup)

      # 2. Cypher Query: Store Scheme Node and Connect to Department, Eligibility, Benefits, & Agency Nodes
      ingest_query = """
            MATCH (d:Department {dep_id: 'Mg=='})
            MERGE (s:Scheme {url: $url})
            SET s.title = $title,
                s.details = $details

            MERGE (d)-[:OFFERS_SCHEME]->(s)

            // Connect Sponsoring Agency
            MERGE (a:Agency {name: $agency})
            MERGE (s)-[:SPONSORED_BY]->(a)

            // Connect Eligibility Criteria Nodes
            FOREACH (elig IN $eligibility_list |
                MERGE (e:Eligibility {description: elig})
                MERGE (s)-[:HAS_ELIGIBILITY]->(e)
            )

            // Connect Benefit Nodes
            FOREACH (ben IN $benefits_list |
                MERGE (b:Benefit {description: ben})
                MERGE (s)-[:PROVIDES_BENEFIT]->(b)
            )

            // Connect Document Nodes
            FOREACH (doc IN $documents_list |
                MERGE (doc_node:Document {name: doc})
                MERGE (s)-[:REQUIRES_DOCUMENT]->(doc_node)
            )
            """

      graph.query(
          ingest_query,
          params={
              "url": detail_url,
              "title": title,
              "details": extracted["full_text"][:1500],  # truncated fallback text
              "agency": extracted["agency"],
              "eligibility_list": extracted["eligibility"],
              "benefits_list": extracted["benefits"],
              "documents_list": extracted["documents"],
          },
      )
      print(f"Ingested Graph Nodes for: {title}")

    except Exception as e:
      print(f"Error reading {detail_url}: {e}")

  print("Ingestion with rich Knowledge Graph structure completed!")


if __name__ == "__main__":
  scrape_and_ingest()