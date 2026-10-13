from dotenv import load_dotenv
from langchain_neo4j import GraphCypherQAChain, Neo4jGraph
from langchain_openai import ChatOpenAI
import os
import streamlit as st

load_dotenv()

st.set_page_config(
    page_title="TN Agriculture Schemes Chatbot", page_icon="🌾", layout="centered"
)

# Sidebar for Language Selection
st.sidebar.title("Settings / அமைப்புகள்")
selected_lang = st.sidebar.selectbox(
    "Choose Language / மொழி:", ["English", "Tamil (தமிழ்)"]
)

st.title("🌾 Tamil Nadu Agriculture Schemes Assistant")
st.markdown(
    "Ask questions about agricultural schemes, eligibility, and benefits"
    " provided by the Government of Tamil Nadu."
)


@st.cache_resource
def get_graph_chain():
  def get_config(key, default=""):
    val = os.getenv(key)
    if val:
      return val
    try:
      if key in st.secrets:
        return st.secrets[key]
    except Exception:
      pass
    return default

  graph = Neo4jGraph(
      url=get_config("NEO4J_URI", "neo4j+s://your-instance.databases.neo4j.io"),
      username=get_config("NEO4J_USERNAME", "neo4j"),
      password=get_config("NEO4J_PASSWORD", "your-password"),
      database=get_config("NEO4J_DATABASE", "neo4j"),
  )

  llm = ChatOpenAI(
      model="gpt-4o-mini",
      temperature=0,
      openai_api_key=get_config("OPENAI_API_KEY", "your-openai-key"),
  )

  chain = GraphCypherQAChain.from_llm(
      llm=llm,
      graph=graph,
      verbose=True,
      allow_dangerous_requests=True,
      return_intermediate_steps=True,
  )
  return chain


try:
  qa_chain = get_graph_chain()
except Exception as e:
  st.error(f"Failed to initialize graph connection: {e}")
  st.stop()

if "messages" not in st.session_state:
  st.session_state.messages = []

for message in st.session_state.messages:
  with st.chat_message(message["role"]):
    st.markdown(message["content"])

if prompt := st.chat_input(
    "e.g., What are the seed distribution schemes available?"
):
  st.session_state.messages.append({"role": "user", "content": prompt})
  with st.chat_message("user"):
    st.markdown(prompt)

  with st.chat_message("assistant"):
    with st.spinner("Searching knowledge graph..."):
      try:
        # Append language instruction if Tamil is selected
        query_text = prompt
        if "Tamil" in selected_lang:
          query_text += (
              " [Important: Translate and provide the final answer clearly in"
              " fluent Tamil language based on the retrieved facts.]"
          )

        response = qa_chain.invoke({"query": query_text})
        answer = response.get("result", "No answer found.")

        sources = set()
        for step in response.get("intermediate_steps", []):
          if isinstance(step, list):
            for item in step:
              if isinstance(item, dict) and "s.url" in item:
                sources.add(item["s.url"])

        full_response = answer
        if sources:
          full_response += "\n\n**Source Links / ஆதார இணைப்புகள்:**\n" + "\n".join(
              [f"- [{url}]({url})" for url in sources]
          )

        st.markdown(full_response)
        st.session_state.messages.append(
            {"role": "assistant", "content": full_response}
        )
      except Exception as e:
        error_msg = f"An error occurred while processing your request: {e}"
        st.error(error_msg)
        st.session_state.messages.append(
            {"role": "assistant", "content": error_msg}
        )