import os
import chromadb
from sentence_transformers import SentenceTransformer

embed_model = SentenceTransformer('all-MiniLM-L6-v2')
chroma_client = chromadb.Client()
collection = chroma_client.get_or_create_collection(name="f1_knowledge")

def load_knowledge_base():
    knowledge_dir = "knowledge_base"
    doc_count = 0
    
    for root, dirs, files in os.walk(knowledge_dir):
        for filename in files:
            if filename.endswith(".txt"):
                filepath = os.path.join(root, filename)
                with open(filepath, 'r') as f:
                    content = f.read()
                
                chunks = [c.strip() for c in content.split('\n\n') if c.strip()]
                
                for i, chunk in enumerate(chunks):
                    doc_id = f"{filename}_{i}"
                    collection.add(
                        documents=[chunk],
                        ids=[doc_id]
                    )
                    doc_count += 1
    
    print(f"Knowledge base loaded: {doc_count} chunks indexed")

def retrieve(query, top_k=3):
    results = collection.query(
        query_texts=[query],
        n_results=top_k
    )
    return results['documents'][0]

load_knowledge_base()