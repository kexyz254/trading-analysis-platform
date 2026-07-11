from setuptools import setup, find_packages

setup(
    name="trading-assistant",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "pandas>=2.0.0",
        "numpy>=1.24.0",
        "ccxt>=4.4.0",
        "requests>=2.30.0",
        "websocket-client>=1.8.0",
        "scikit-learn>=1.5.0",
        "scipy>=1.13.0",
        "xgboost>=2.1.0",
        "shap>=0.51.0",
        "joblib>=1.4.0",
        "python-dotenv>=1.0.0",
        "PyYAML>=6.0.0",
        "streamlit>=1.38.0",
        "plotly>=5.24.0",
        "fastapi>=0.115.0",
        "uvicorn>=0.30.0",
    ],
    python_requires=">=3.9",
)
