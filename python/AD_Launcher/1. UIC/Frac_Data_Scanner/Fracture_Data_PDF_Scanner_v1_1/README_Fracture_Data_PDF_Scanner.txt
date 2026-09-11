FRACTURE DATA PDF SCANNER - VERSION 1.1
=======================================

QUICK START
-----------
1. Extract the entire ZIP file to a normal writable folder.
   Do not run the program from inside the ZIP file.

2. Double-click:

   Start_Fracture_Data_Scanner.bat

3. On the first run, the launcher will automatically:
   - confirm that Python 3.10 or newer is available;
   - create a local virtual environment in the .venv folder;
   - check all required Python packages;
   - install missing packages from requirements_fracture_data_scanner.txt;
   - run pip check to verify the environment;
   - look for Tesseract OCR;
   - offer to install Tesseract through Windows Package Manager when it is
     missing; and
   - launch the scanner with the Python interpreter inside .venv.

Later launches normally skip package installation after the environment passes
its verification checks.

IMPORTANT FILES
---------------
Start_Fracture_Data_Scanner.bat
    Recommended one-click launcher. It creates/checks the virtual environment,
    installs missing dependencies, and starts the GUI.

Run_Fracture_Data_Scanner.bat
    Compatibility shortcut. It calls Start_Fracture_Data_Scanner.bat.

Install_Fracture_Scanner_Requirements.bat
    Creates or verifies the virtual environment without launching the GUI.

Repair_or_Update_Environment.bat
    Deletes and recreates .venv, then reinstalls every required package. Use
    this if setup was interrupted, imports fail, or a package becomes damaged.

bootstrap_fracture_scanner.py
    Standard-library-only setup program used by the batch files. It records
    detailed setup output in setup_log.txt.

fracture_data_pdf_scanner.py
    Main scanner application. Running this file directly also redirects through
    the managed virtual-environment setup when bootstrap_fracture_scanner.py is
    present.

requirements_fracture_data_scanner.txt
    Required Python packages: PyMuPDF, openpyxl, pytesseract, and Pillow.

VIRTUAL ENVIRONMENT BEHAVIOR
----------------------------
- The .venv folder is created next to the application files.
- Python packages are installed only inside .venv; the launcher does not modify
  your global Python package installation.
- The ZIP intentionally does not contain a prebuilt .venv because Python
  virtual environments are computer- and folder-specific.
- If you move the application folder after setup, the launcher detects the move
  and rebuilds the environment as needed.
- To reset everything, close the scanner and run
  Repair_or_Update_Environment.bat.

TESSERACT OCR
-------------
Tesseract is a separate Windows application, not only a Python package. It is
needed for old CalGEM well files whose pages are scanned images without an
embedded text layer.

At first launch, the setup program checks common installation locations. When
Tesseract is missing and Windows Package Manager (winget) is available, the
launcher offers to install it. The installation may require company approval or
administrator permission.

If automatic installation is unavailable, install Tesseract manually and then
select tesseract.exe in the GUI. A common location is:

   C:\Program Files\Tesseract-OCR\tesseract.exe

When Tesseract is not found, the scanner still opens, but OCR is disabled by
default. Searchable PDFs can still be scanned.

NETWORK AND COMPANY-COMPUTER NOTES
----------------------------------
The first package installation requires access to a Python package source.
Your company proxy, VPN, firewall, or software policy may control that access.
The launcher uses the pip configuration already installed on your computer, so
an approved internal Python package mirror will be used automatically when it
is configured.

If setup fails:
1. Review setup_log.txt in the application folder.
2. Connect to the required company network or VPN, if applicable.
3. Run Repair_or_Update_Environment.bat.
4. If pip is blocked by policy, provide setup_log.txt to your IT support team.

WHAT THE SCANNER FINDS
----------------------
The program scans PDF well files for likely evidence of:
- step-rate tests;
- mini-frac or minifrac tests;
- DFITs;
- leak-off or formation-integrity tests;
- hydraulic-fracture treatment records;
- fracture-gradient values;
- ISIP, closure pressure, breakdown pressure, and treating pressure;
- injection/pump rates; and
- proppant or sand data.

The program extracts embedded PDF text first. When OCR is enabled, it uses
Tesseract only on pages that contain little or no embedded text.

RECOMMENDED SCAN SETTINGS
-------------------------
- Include subfolders: checked
- OCR image-only pages: checked when Tesseract is installed
- OCR DPI: 150
- Parallel PDF workers: 2
- Stop a PDF after strong evidence is found: checked

Use 200 DPI for faint scans. Uncheck early stopping when you want every matching
page rather than only enough evidence to classify the PDF.

OUTPUT
------
The Excel export contains:
- PDF Summary: one row per PDF;
- Match Details: matched pages, categories, wording, and snippets;
- Well Summary: strongest result grouped by API number or well name; and
- Settings: scan settings, application version, and managed Python path.

RESULT MEANINGS
---------------
YES - Test/treatment data
    Strong evidence of an actual test or hydraulic-fracture treatment with
    numeric pressure, rate, gradient, ISIP, or related data.

POSSIBLE - Review
    Relevant evidence was found, but the file should be reviewed manually.

POSSIBLE - Planned only
    The file appears to contain a proposed completion or treatment program,
    not a clear record that the work was performed.

REFERENCE/ASSUMED ONLY
    A fracture-gradient value appears to be assumed, estimated, permitted, or
    used as a reference rather than measured by a test.

NO CLEAR DATA
    No clear fracture-test or treatment evidence was found. For scanned PDFs,
    confirm that OCR was enabled and Tesseract was available.

ENGINEERING REVIEW
------------------
This application is a screening tool. OCR may misread decimals, units, or
numbers. Review the original PDF before relying on a value for engineering or
regulatory work.
