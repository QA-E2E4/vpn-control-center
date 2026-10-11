using System;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

public static class Validation
{
    static int checks;
    static void Check(bool ok, string name) { if (!ok) throw new Exception(name); checks++; }
    [STAThread]
    public static void Main(string[] args)
    {
        string directory = Path.Combine(Path.GetTempPath(), "openflux-tests-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        try
        {
            string keyPath = Path.Combine(directory, "ключ with spaces"); File.WriteAllText(keyPath, "DUMMY-NOT-A-KEY");
            ConnectionSettings s = new ConnectionSettings { Host = "192.0.2.10", User = "admin", KeyPath = keyPath };
            Check(ConnectionTools.Validate(s) == null, "valid IPv4/key path");
            s.Host = "vpn.example.org"; Check(ConnectionTools.Validate(s) == null, "valid DNS");
            s.Host = "2001:db8::1"; Check(ConnectionTools.Validate(s) == null, "valid IPv6");
            foreach (string bad in new string[] { "-oProxyCommand=evil", "x y", "https://example.org", "a\nb", "example.org:22", "a&b" })
            { s.Host = bad; Check(ConnectionTools.Validate(s) != null, "reject host " + bad); }
            s.Host = "vpn.example.org"; s.User = "-ofoo"; Check(ConnectionTools.Validate(s) != null, "reject injected user");
            s.User = "admin"; s.Port = 0; Check(ConnectionTools.Validate(s) != null, "reject port");
            s.Port = 22; s.KeyPath = keyPath + ".pub"; File.WriteAllText(s.KeyPath, "public");
            Check(ConnectionTools.Validate(s) != null, "reject public key");
            s.KeyPath = keyPath;
            Check(ConnectionTools.Quote("x y") == "\"x y\"", "quote spaces");
            Check(ConnectionTools.Quote("x\"y") == "\"x\\\"y\"", "quote embedded quote");
            Check(ConnectionTools.Quote("x\\") == "\"x\\\\\"", "quote trailing slash");
            Check(ConnectionTools.NormalizeKeyPath("  \"" + keyPath + "\"  ") == keyPath, "key path quotes and outer whitespace");
            Check(ConnectionTools.NormalizeKeyPath(keyPath) == keyPath, "plain key path");
            Check(ConnectionTools.NormalizeKeyPath("") == "", "empty key path");
            Environment.SetEnvironmentVariable("OPENFLUX_TEST_KEY_PATH", keyPath);
            try { Check(ConnectionTools.NormalizeKeyPath("%OPENFLUX_TEST_KEY_PATH%") == keyPath, "key path environment variable"); }
            finally { Environment.SetEnvironmentVariable("OPENFLUX_TEST_KEY_PATH", null); }
            string config = Path.Combine(directory, "settings.json");
            ConnectionTools.Save(config, s); s.Host = "second.example.org"; ConnectionTools.Save(config, s);
            ConnectionSettings loaded = ConnectionTools.Load(config);
            Check(loaded.Host == s.Host && loaded.KeyPath == keyPath && loaded.Port == 22 && loaded.User == "admin", "save/update/load settings");
            Check(!File.ReadAllText(config).Contains("DUMMY-NOT-A-KEY"), "private key never stored");
            string ssh = ConnectionTools.Arguments(s);
            Check(ssh.Contains("127.0.0.1:8787:127.0.0.1:8787") && ssh.Contains("StrictHostKeyChecking=yes") && ssh.Contains("BatchMode=yes"), "secure forwarding options");
            Check(ssh.Contains(ConnectionTools.Quote(keyPath)), "key path argument");
            Console.WriteLine(checks + " checks passed");
            if (args.Length >= 1)
            {
                Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
                using (OpenFluxWindow window = new OpenFluxWindow(null))
                {
                    if(args.Length > 1) window.Scale(new SizeF(1.5F,1.5F));
                    // Show a Windows example path in the preview only.
                    var field = typeof(OpenFluxWindow).GetField("key", BindingFlags.NonPublic | BindingFlags.Instance);
                    ((TextBox)field.GetValue(window)).Text = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @".ssh\id_ed25519");
                    using (Timer capture = new Timer { Interval = 3000 })
                    {
                        capture.Tick += delegate {
                            capture.Stop();
                            using (Bitmap bitmap = new Bitmap(window.Width, window.Height))
                            { window.DrawToBitmap(bitmap, new Rectangle(0, 0, bitmap.Width, bitmap.Height)); bitmap.Save(args[0]); }
                            window.Close();
                        };
                        capture.Start(); Application.Run(window);
                    }
                }
                Console.WriteLine("Window rendered");
            }
        }
        finally { Directory.Delete(directory, true); }
    }
}
