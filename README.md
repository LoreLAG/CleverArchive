# CleverArchive - AI Document Management System

## 1. Overview
CleverArchive is a robust, Python-based desktop application designed to index, search, and interrogate large volumes of technical PDF documents. Built with a Tkinter graphical interface, it leverages Google Gemini AI models to perform advanced OCR data extraction, vector embedding generation, and Retrieval-Augmented Generation (RAG). It allows users to ask complex questions in natural language and receive highly accurate, context-aware answers grounded strictly in the proprietary document database.

## 2. Core Capabilities
* **Intelligent OCR & Parsing:** Utilizes Gemini Flash models to extract structured text from complex PDFs (e.g., dual-column, multi-language documents), completely ignoring irrelevant metadata like signatures or dates.
* **Semantic & Hybrid Search:** Combines mathematical cosine similarity (via vector embeddings) with a proprietary hybrid keyword override system. This ensures that exact alphanumeric codes (e.g., part numbers) are instantly prioritized alongside semantic matches.
* **Retrieval-Augmented Generation (RAG):** The built-in "Analyst" model (Gemini Pro) synthesizes precise, narrative answers using *only* the context of the retrieved documents, significantly minimizing AI hallucinations.
* **Smart Synchronization:** Employs a local SQLite tracking system to monitor file modification timestamps (`mtime`). It only processes new or modified PDFs, drastically reducing execution time and API costs.
* **Encrypted Network Sync:** Securely encrypts the Google Gemini API key and system password using XOR encryption and Base64. It synchronizes these credentials across multiple workstations via a shared network drive.
* **Granular Folder Control:** Features a custom interactive folder tree widget, allowing users to selectively include or exclude specific directories from the indexing and searching scope.
* **Cost Tracking & Debugging:** Includes an integrated token counter that estimates API costs based on user-configurable pricing tiers (supports <128K and >128K context window rates).

## 3. System Requirements
* **Operating System:** Optimized for Microsoft Windows (includes specific bypasses for UNC network drives and SMB paths).
* **Environment:** Python 3.x execution environment.
* **Dependencies:** * `google-genai`
  * `pypdf`
  * `numpy`
  * `thefuzz`
  * standard libraries (`tkinter`, `sqlite3`, `concurrent.futures`, etc.)

## 4. User Manual

### 4.1 Initial Setup (Settings Tab)
1. **Database & Folder Selection:** Choose a target SQLite database file (or create a new one) and select the root directory containing your PDF files.
2. **Folder Selection:** Use the interactive folder tree to check the specific subfolders you wish to analyze.
3. **API Configuration:** Enter your Google Gemini API Key. To do this, click "Edit", provide the system password, and insert the key. Click "Save" to encrypt and distribute it across the network.
4. **AI Models:** The system will automatically detect and select the best available models for Extraction (Flash), Analysis (Pro), and Vectorization (Embedding). You can manually override these by clicking the "+" button.

### 4.2 Data Processing (Extraction & Vectorization)
Navigate to the "Data Processing" tab to build your knowledge base. If the application detects a mismatch between the files on your disk and the database, a red warning banner will appear.
1. **Text Extraction:** Click "Start Extraction". The system will batch-upload new/modified PDFs to the Gemini API, forcing it to return a clean, structured JSON array of the document's content.
2. **Vector Processing:** Once extraction is complete, click "Start Vectorization". The system will automatically chunk the text (with overlap) and generate mathematical vector embeddings for semantic search.

### 4.3 Querying the Database (Query Tab)
1. **Search:** Type a technical question or a part number in the "Question" field and press Enter (or click "Process").
2. **Filters (Optional):** Click "Select Folders" to narrow down the search scope to specific directories for faster and more targeted results.
3. **Results:** * The AI will provide a synthesized response addressing your query.
   * Below the response, the system will list the most relevant source files ranked by an "Affinity" percentage.
   * Click **Open PDF** (or **Open Word** if a matching `.docx` is found in the same folder) to directly access the source documentation.
