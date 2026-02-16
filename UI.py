import streamlit as st
import base64

# --- 1. Header Gradient Function ---
import streamlit as st
import base64

def set_header_content(logo_path, main_title, sub_title, r, g, b, opacity):
    """
    Sets the header style using CSS Pseudo-elements to ensure text moves with the sidebar.
    """
    # 1. Convert Logo to Base64
    try:
        with open(logo_path, "rb") as f:
            encoded_logo = base64.b64encode(f.read()).decode()
        logo_css = f"url(data:image/png;base64,{encoded_logo})"
    except FileNotFoundError:
        logo_css = "none"

    # 2. Define Background Color
    bg_color = f"rgba({r}, {g}, {b}, {opacity})"

    # 3. Inject CSS
    st.markdown(
        f"""
        <style>
        /* A. HEADER BASE STYLE */
        header[data-testid="stHeader"] {{
            /* Logo Position: 60px from left */
            background: {logo_css} no-repeat 60px center / 50px auto, 
                        {bg_color} !important;
            
            /* Make header slightly taller to fit two lines of text nicely */
            height: 4.5rem !important; 
        }}

        /* B. MAIN TITLE (Using ::before) */
        header[data-testid="stHeader"]::before {{
            content: "{main_title}";
            
            position: absolute;
            left: 125px;      /* Align next to logo */
            top: 18px;        /* Position near top */
            
            font-family: 'Source Sans Pro', sans-serif;
            font-size: 22px;
            font-weight: 700;
            color: white;
            z-index: 999;
        }}

        /* C. SUB TITLE (Using ::after) */
        header[data-testid="stHeader"]::after {{
            content: "{sub_title}";
            
            position: absolute;
            left: 125px;      /* Align same as title */
            top: 46px;        /* Position below title */
            
            font-family: 'Source Sans Pro', sans-serif;
            font-size: 13px;
            font-weight: 400;
            color: #d0d0d0;   /* Light Gray */
            text-transform: uppercase;
            letter-spacing: 1px;
            z-index: 999;
        }}

        /* D. Force Hamburger Menu White */
        .st-emotion-cache-12fmw14 {{
            color: white !important;
        }}
        </style>
        """,
        unsafe_allow_html=True
    )

# --- 2. Background Image Function ---
def set_background_image(image_file):
    """Sets the main app background image."""
    try:
        with open(image_file, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        st.markdown(
            f"""
            <style>
            .stApp {{
                background-image: url(data:image/png;base64,{data});
                background-size: cover;
                background-position: center;
                background-repeat: no-repeat;
                background-attachment: fixed;
            }}
            </style>
            """,
            unsafe_allow_html=True
        )
    except FileNotFoundError:
        pass
