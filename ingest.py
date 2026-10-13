import os
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


def scrape_and_ingest():
  print("Fetching main agriculture scheme list...")
  response = requests.get(LIST_URL, timeout=15)
  if response.status_code != 200:
    print(f"Failed to fetch scheme list. Status code: {response.status_code}")
    return

  soup = BeautifulSoup(response.text, "html.parser")

  # Create Department node
  graph.query("""
    MERGE (d:Department {name: 'Agriculture and Farmers Welfare'})
    SET d.dep_id = 'Mg=='
    RETURN d
    """)

  # Collect all scheme links
  links = soup.find_all("a", href=True)
  scheme_links = list(
      set([
          link["href"]
          for link in links
          if "scheme_details.php" in link["href"]
      ])
  )

  print(f"Found {len(scheme_links)} detail links. Ingesting content...")

  for link in scheme_links:
    detail_url = (
        link if link.startswith("http") else BASE_URL + link.lstrip("/")
    )
    try:
      res = requests.get(detail_url, timeout=10)
      if res.status_code != 200:
        continue

      detail_soup = BeautifulSoup(res.text, "html.parser")

      # Extract specific scheme title and table details
      title_elem = detail_soup.find(["h1", "h2", "h3", "strong", "b"])
      title = (
          title_elem.get_text(strip=True)
          if title_elem
          else "Agricultural Scheme"
      )

      # Extract tabular/text details specifically, avoiding footer text
      content_list = []
      tables = detail_soup.find_all("table")
      if tables:
        for table in tables:
          content_list.append(table.get_text(separator=" ", strip=True))

      if not content_list:
        paragraphs = [
            p.get_text(strip=True)
            for p in detail_soup.find_all("p")
            if "NIC" not in p.text and "Disclaimer" not in p.text
        ]
        content_list = paragraphs

      content = (
          " ".join(content_list)
          if content_list
          else "No specific details available."
      )

      # Store inside Neo4j
      ingest_query = """
            MATCH (d:Department {dep_id: 'Mg=='})
            MERGE (s:Scheme {url: $url})
            SET s.title = $title,
                s.details = $details
            MERGE (d)-[:OFFERS_SCHEME]->(s)
            """
      graph.query(
          ingest_query,
          params={"url": detail_url, "title": title, "details": content},
      )
      print(f"Successfully ingested: {title}")

    except Exception as e:
      print(f"Error reading {detail_url}: {e}")

  print("Ingestion sequence finished!")


if __name__ == "__main__":
  scrape_and_ingest()