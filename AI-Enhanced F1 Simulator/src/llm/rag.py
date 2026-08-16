import os
import chromadb
from sentence_transformers import SentenceTransformer

# Load the model
embed_model = SentenceTransformer('all-MiniLM-L6-v2')
chroma_client = chromadb.Client()
collection = chroma_client.get_or_create_collection(name="f1_knowledge")

# Flag for load status
_IS_LOADED = False

def load_knowledge_base():
    global _IS_LOADED

    # If the model is already loaded, then return
    if _IS_LOADED:
        return

    # If ChromaDB already has data, mark as loaded and return
    if collection.count() > 0:
        _IS_LOADED = True
        return

    knowledge_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge_base")
    doc_count = 0
    
    for root, dirs, files in os.walk(knowledge_dir):
        for filename in files:
            if filename.endswith(".txt"):
                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, knowledge_dir)
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()

                chunks = [c.strip() for c in content.split('\n\n') if c.strip()]

                for i, chunk in enumerate(chunks):
                    doc_id = f"{rel_path}_{i}"
                    collection.add(
                        documents=[chunk],
                        ids=[doc_id]
                    )
                    doc_count += 1
    
    _IS_LOADED = True
    print(f"Knowledge base loaded: {doc_count} chunks indexed")

def retrieve(query, top_k=3):
    # Make sure the knowledge base is loaded before querying
    if collection.count() == 0:
        load_knowledge_base()

    results = collection.query(
        query_texts=[query],
        n_results=top_k
    )
    return results['documents'][0]