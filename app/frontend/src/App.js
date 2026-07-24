

// This app is a pure-Python (FastAPI + Jinja2) server-rendered application.
// The React shell simply redirects the bare domain to the Python app under /api/.
function App() {
  useEffect(() => {
    window.location.replace(\"/api/\");
  }, []);
  return (
    <div
      data-testid=\"redirect-splash\"
      style={{
        minHeight: \"100vh\",
        display: \"flex\",
        alignItems: \"center\",
        justifyContent: \"center\",
        fontFamily: \"system-ui, sans-serif\",
        color: \"#0F2A3F\",
        background: \"#FBF9F4\",
      }}
    >
      Redirecting to Sparkle Dental…
    </div>
  );
}

export default App;

