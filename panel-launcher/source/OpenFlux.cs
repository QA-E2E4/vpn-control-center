using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyVersion("0.3.0.0")]
[assembly: System.Reflection.AssemblyFileVersion("0.3.0.0")]
[assembly: System.Runtime.Versioning.TargetFramework(".NETFramework,Version=v4.5")]

[DataContract]
public class ConnectionSettings
{
    [DataMember] public string Host = "";
    [DataMember] public int Port = 22;
    [DataMember] public string User = "";
    [DataMember] public string KeyPath = "";
}

public static class ConnectionTools
{
    public static string NormalizeKeyPath(string value)
    {
        // Explicit array binds to the .NET Framework overload, including on Mono.
        return Environment.ExpandEnvironmentVariables(value.Trim().Trim(new char[] { '"' }));
    }
    // Windows CommandLineToArgvW / CRT quoting. Never invoke a command shell.
    public static string Quote(string value)
    {
        StringBuilder b = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') b.Append('\\', slashes * 2 + 1).Append('"');
            else b.Append('\\', slashes).Append(c);
            slashes = 0;
        }
        return b.Append('\\', slashes * 2).Append('"').ToString();
    }
    public static string Validate(ConnectionSettings s)
    {
        IPAddress ip;
        bool hostValid = !String.IsNullOrEmpty(s.Host) && s.Host.Length <= 253 &&
            (IPAddress.TryParse(s.Host, out ip) || Regex.IsMatch(s.Host,
            @"\A[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)*\z"));
        if (!hostValid) return "Введите IP-адрес или имя сервера без https://, порта и пробелов.";
        if (s.Port < 1 || s.Port > 65535) return "SSH-порт должен быть от 1 до 65535.";
        if (String.IsNullOrEmpty(s.User) || !Regex.IsMatch(s.User, @"\A[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,63}\z"))
            return "Проверьте имя пользователя SSH, например admin.";
        if (String.IsNullOrWhiteSpace(s.KeyPath) || !File.Exists(s.KeyPath)) return "Выберите существующий файл приватного SSH-ключа.";
        if (s.KeyPath.EndsWith(".pub", StringComparison.OrdinalIgnoreCase)) return "Нужен приватный ключ — файл без расширения .pub.";
        return null;
    }
    public static string Arguments(ConnectionSettings s)
    {
        string[] args = { "-N", "-T", "-L", "127.0.0.1:8787:127.0.0.1:8787", "-p", s.Port.ToString(),
            "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3", "-i", s.KeyPath, "-l", s.User, s.Host };
        return String.Join(" ", args.Select(Quote));
    }
    public static void Save(string path, ConnectionSettings settings)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path));
        string tmp = path + ".tmp";
        using (FileStream f = File.Create(tmp)) new DataContractJsonSerializer(typeof(ConnectionSettings)).WriteObject(f, settings);
        if (File.Exists(path)) File.Replace(tmp, path, null); else File.Move(tmp, path);
    }
    public static ConnectionSettings Load(string path)
    {
        using (FileStream f = File.OpenRead(path))
            return (ConnectionSettings)new DataContractJsonSerializer(typeof(ConnectionSettings)).ReadObject(f);
    }
}

public static class UiTheme
{
    public static readonly Color Background = Color.FromArgb(12, 17, 24);
    public static readonly Color Surface = Color.FromArgb(21, 29, 40);
    public static readonly Color Field = Color.FromArgb(14, 21, 31);
    public static readonly Color Border = Color.FromArgb(44, 58, 76);
    public static readonly Color Text = Color.FromArgb(236, 242, 249);
    public static readonly Color Muted = Color.FromArgb(153, 170, 192);
    public static readonly Color Accent = Color.FromArgb(183, 239, 136);
    public static GraphicsPath Rounded(RectangleF r, float radius)
    {
        float d = Math.Min(radius * 2, Math.Min(r.Width, r.Height));
        GraphicsPath p = new GraphicsPath();
        p.AddArc(r.X, r.Y, d, d, 180, 90); p.AddArc(r.Right-d, r.Y, d, d, 270, 90);
        p.AddArc(r.Right-d, r.Bottom-d, d, d, 0, 90); p.AddArc(r.X, r.Bottom-d, d, d, 90, 90);
        p.CloseFigure(); return p;
    }
}
public class SurfacePanel : Panel
{
    public Color FillColor = UiTheme.Surface;
    public bool FocusBorder;
    public int Radius = 16;
    public SurfacePanel() { DoubleBuffered = true; BackColor = UiTheme.Background; }
    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e); e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
        using (GraphicsPath p = UiTheme.Rounded(new RectangleF(1, 1, Width-3, Height-3), Radius))
        using (SolidBrush b = new SolidBrush(FillColor))
        using (Pen pen = new Pen(FocusBorder && ContainsFocus ? UiTheme.Accent : UiTheme.Border, 1))
        { e.Graphics.FillPath(b, p); e.Graphics.DrawPath(pen, p); }
    }
}
public class ModernButton : Button
{
    public bool Primary;
    public bool Quiet;
    bool hover, pressed;
    public ModernButton()
    {
        SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        FlatStyle = FlatStyle.Flat; FlatAppearance.BorderSize = 0;
        Cursor = Cursors.Hand; BackColor = UiTheme.Background; ForeColor = UiTheme.Text;
    }
    protected override void OnMouseEnter(EventArgs e) { hover=true; Invalidate(); base.OnMouseEnter(e); }
    protected override void OnMouseLeave(EventArgs e) { hover=false; pressed=false; Invalidate(); base.OnMouseLeave(e); }
    protected override void OnMouseDown(MouseEventArgs e) { pressed=true; Invalidate(); base.OnMouseDown(e); }
    protected override void OnMouseUp(MouseEventArgs e) { pressed=false; Invalidate(); base.OnMouseUp(e); }
    protected override void OnGotFocus(EventArgs e) { base.OnGotFocus(e); Invalidate(); }
    protected override void OnLostFocus(EventArgs e) { base.OnLostFocus(e); Invalidate(); }
    protected override void OnEnabledChanged(EventArgs e) { base.OnEnabledChanged(e); Invalidate(); }
    protected override void OnPaint(PaintEventArgs e)
    {
        e.Graphics.Clear(BackColor); e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
        Color fill = Primary ? UiTheme.Accent : Color.FromArgb(28, 39, 54);
        if (Quiet) fill = BackColor;
        if (!Enabled) fill = Color.FromArgb(23, 31, 42);
        else if (pressed) fill = Primary ? Color.FromArgb(159, 215, 112) : Color.FromArgb(43, 59, 76);
        else if (hover) fill = Primary ? Color.FromArgb(201, 252, 159) : Color.FromArgb(36, 49, 65);
        using (GraphicsPath path = UiTheme.Rounded(new RectangleF(1,1,Width-3,Height-3),12))
        using (SolidBrush brush = new SolidBrush(fill))
        using (Pen pen = new Pen(Focused && Enabled ? UiTheme.Accent : Quiet ? fill : Primary && Enabled ? fill : UiTheme.Border))
        { e.Graphics.FillPath(brush,path); e.Graphics.DrawPath(pen,path); }
        Color text = !Enabled ? Color.FromArgb(95,111,132) : Primary ? Color.FromArgb(19,35,26) : Quiet ? UiTheme.Muted : UiTheme.Text;
        TextRenderer.DrawText(e.Graphics, Text, Font, ClientRectangle, text,
            TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis);
    }
}
public class StatusDot : Control
{
    public Color DotColor = UiTheme.Muted;
    public StatusDot() { SetStyle(ControlStyles.SupportsTransparentBackColor | ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer, true); BackColor=Color.Transparent; }
    protected override void OnPaint(PaintEventArgs e)
    {
        e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;
        using(SolidBrush halo=new SolidBrush(Color.FromArgb(35,DotColor))) e.Graphics.FillEllipse(halo,0,0,Width-1,Height-1);
        using(SolidBrush dot=new SolidBrush(DotColor)) e.Graphics.FillEllipse(dot,Width/3F,Height/3F,Width/3F,Height/3F);
    }
}

public class OpenFluxWindow : Form
{
    static readonly string Folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "OpenFluxPanel");
    static readonly string SettingsPath = Path.Combine(Folder, "settings.json");
    const string PanelUrl = "http://127.0.0.1:8787/";
    readonly TextBox host = new TextBox(), user = new TextBox(), key = new TextBox();
    readonly NumericUpDown port = new NumericUpDown();
    readonly ModernButton browse = new ModernButton(), connect = new ModernButton(), disconnect = new ModernButton(), open = new ModernButton(), save = new ModernButton();
    readonly Label status = new Label();
    readonly Label stateTitle = new Label();
    readonly StatusDot stateDot = new StatusDot();
    readonly NotifyIcon tray = new NotifyIcon();
    readonly Icon mainIcon = LoadIcon("main", 32);
    readonly Icon onlineIcon = LoadIcon("main", 16);
    readonly Icon offlineIcon = LoadIcon("offline", 16);
    readonly Icon connectingIcon = LoadIcon("connecting", 16);
    readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    readonly object logLock = new object();
    readonly StringBuilder log = new StringBuilder();
    readonly EventWaitHandle showRequest;
    Process tunnel;
    bool connecting, connected, exiting;
    int generation;

    public OpenFluxWindow(EventWaitHandle request)
    {
        showRequest = request;
        Text = "OpenFlux 0.3.0 — подключение к панели";
        Font = new Font("Segoe UI", 10F);
        BackColor = UiTheme.Background; ForeColor = UiTheme.Text;
        AutoScaleDimensions = new SizeF(96,96); AutoScaleMode = AutoScaleMode.Dpi;
        ClientSize = new Size(760,720); AutoScroll = true;
        FormBorderStyle = FormBorderStyle.FixedSingle; MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen; Icon = mainIcon;
        DoubleBuffered = true;
        Load += delegate {
            Rectangle area = Screen.FromControl(this).WorkingArea;
            if (Height > area.Height-24) { Height=area.Height-24; Top=area.Top+12; }
            if (Width > area.Width-24) { Width=area.Width-24; Left=area.Left+12; }
            ApplyDarkTitleBar();
        };
        PictureBox logo = new PictureBox { Left=32, Top=25, Width=48, Height=48, SizeMode=PictureBoxSizeMode.Zoom, Image=BrandBitmap() };
        logo.Disposed += delegate { if(logo.Image!=null) logo.Image.Dispose(); }; Controls.Add(logo);
        LabelText(this,"OpenFlux",94,18,390,46,24,true,UiTheme.Text);
        LabelText(this,"Личная панель управления",96,64,400,22,10,false,UiTheme.Muted);
        LabelText(this,"DESKTOP  /  0.3.0",550,37,178,24,9,true,UiTheme.Muted).TextAlign=ContentAlignment.MiddleRight;

        SurfacePanel stateCard = new SurfacePanel { Left=32,Top=108,Width=696,Height=82,Radius=18 };
        Controls.Add(stateCard);
        stateDot.SetBounds(20,23,32,32); stateDot.BackColor=UiTheme.Surface; stateCard.Controls.Add(stateDot);
        stateTitle.SetBounds(68,15,600,27); stateTitle.Text="Готово к подключению"; stateTitle.Font=new Font("Segoe UI",12,FontStyle.Bold);
        stateTitle.BackColor=UiTheme.Surface; stateTitle.ForeColor=UiTheme.Text; stateCard.Controls.Add(stateTitle);
        status.SetBounds(68,44,600,30); status.Font=new Font("Segoe UI",9F); status.BackColor=UiTheme.Surface; status.ForeColor=UiTheme.Muted;
        status.Text="Укажите сервер и ключ, чтобы открыть панель."; stateCard.Controls.Add(status);

        SurfacePanel card = new SurfacePanel { Left=32,Top=210,Width=696,Height=340,Radius=18 };
        Controls.Add(card);
        LabelText(card,"ПАРАМЕТРЫ ПОДКЛЮЧЕНИЯ",24,23,400,23,9,true,UiTheme.Muted);
        SetupButton(save,"Сохранить настройки",494,13,178,delegate { SaveSettings(); },card,40); save.Quiet=true;
        LabelText(card,"Адрес VPS",24,64,410,24,10,true,UiTheme.Text);
        Field(card,host,24,94,458);
        LabelText(card,"SSH-порт",498,64,170,24,10,true,UiTheme.Text);
        port.Minimum=1; port.Maximum=65535; port.Value=22;
        Field(card,port,498,94,174);
        LabelText(card,"Пользователь",24,146,640,24,10,true,UiTheme.Text);
        Field(card,user,24,176,648);
        LabelText(card,"Приватный SSH-ключ",24,228,640,24,10,true,UiTheme.Text);
        Field(card,key,24,258,506);
        SetupButton(browse,"Обзор…",542,258,130,delegate { ChooseKey(); },card,44);
        LabelText(card,"Ключ остаётся на компьютере. Сохраняется только путь к файлу.",24,310,648,20,9,false,UiTheme.Muted);

        SetupButton(connect,"Подключиться",32,570,224,async delegate { await Connect(); },this,48); connect.Primary=true;
        SetupButton(open,"Открыть панель",268,570,224,delegate { OpenPanel(); },this,48); open.Enabled=false;
        SetupButton(disconnect,"Отключить",504,570,224,delegate { Disconnect(); },this,48); disconnect.Enabled=false;
        Panel separator=new Panel { Left=32,Top=644,Width=696,Height=1,BackColor=UiTheme.Border }; Controls.Add(separator);
        LinkLabel help=FooterLink("Первое подключение и помощь",32,660,430); help.LinkClicked+=delegate { ShowHelp(); };
        LinkLabel logs=FooterLink("Журнал SSH",616,660,112); logs.LinkClicked+=delegate { ShowLog(); };
        LabelText(this,"После подключения окно можно закрыть — OpenFlux останется в трее.",32,692,696,20,8.5F,false,UiTheme.Muted);

        ContextMenuStrip menu = new ContextMenuStrip { BackColor=UiTheme.Surface, ForeColor=UiTheme.Text, ShowImageMargin=false };
        menu.Items.Add("Открыть панель", null, delegate { OpenPanel(); });
        menu.Items.Add("Настройки подключения", null, delegate { Restore(); });
        menu.Items.Add("Отключить туннель", null, delegate { Disconnect(); Restore(); });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Выход", null, delegate { exiting = true; Close(); });
        tray.Icon = offlineIcon; tray.Text = "OpenFlux — настройки подключения"; tray.ContextMenuStrip = menu;
        tray.DoubleClick += delegate { Restore(); }; tray.Visible = true;
        FormClosing += OnClosing;
        Resize += delegate { if (WindowState == FormWindowState.Minimized) Hide(); };
        timer.Interval = 700; timer.Tick += Tick; timer.Start();

        ConnectionSettings s = new ConnectionSettings();
        if (File.Exists(SettingsPath))
        {
            try { s = ConnectionTools.Load(SettingsPath); if (s == null) throw new IOException(); }
            catch { status.Text = "Не удалось прочитать настройки. Заполните их заново."; }
        }
        host.Text = s.Host; user.Text = s.User; key.Text = s.KeyPath;
        port.Value = Math.Max(1, Math.Min(65535, s.Port));
        AcceptButton = connect;
        Shown += delegate { host.Focus(); };
    }
    static Bitmap BrandBitmap()
    {
        using (Icon icon=LoadIcon("main",64)) return icon.ToBitmap();
    }
    static Icon LoadIcon(string name, int size)
    {
        using (Stream stream = System.Reflection.Assembly.GetExecutingAssembly().GetManifestResourceStream("OpenFlux." + name + ".ico"))
        {
            if (stream == null) throw new InvalidOperationException("Missing application icon: " + name);
            using (Icon icon = new Icon(stream, new Size(size, size))) return (Icon)icon.Clone();
        }
    }
    protected override void Dispose(bool disposing)
    {
        if (disposing) { tray.Dispose(); mainIcon.Dispose(); onlineIcon.Dispose(); offlineIcon.Dispose(); connectingIcon.Dispose(); timer.Dispose(); }
        base.Dispose(disposing);
    }
    [DllImport("dwmapi.dll")]
    static extern int DwmSetWindowAttribute(IntPtr hwnd,int attribute,ref int value,int size);
    void ApplyDarkTitleBar()
    {
        if (Environment.OSVersion.Platform != PlatformID.Win32NT) return;
        try { int enabled=1; if (DwmSetWindowAttribute(Handle,20,ref enabled,4)!=0) DwmSetWindowAttribute(Handle,19,ref enabled,4); } catch { }
    }
    static Label LabelText(Control parent,string text,int x,int y,int width,int height,float size,bool bold,Color color)
    {
        Label label=new Label { Text=text,Left=x,Top=y,Width=width,Height=height,Font=new Font("Segoe UI",size,bold?FontStyle.Bold:FontStyle.Regular),ForeColor=color,BackColor=parent is SurfacePanel?UiTheme.Surface:UiTheme.Background };
        parent.Controls.Add(label); return label;
    }
    void Field(Control parent,Control input,int x,int y,int width)
    {
        SurfacePanel frame=new SurfacePanel { Left=x,Top=y,Width=width,Height=44,Radius=11,FillColor=UiTheme.Field,FocusBorder=true,BackColor=UiTheme.Surface };
        parent.Controls.Add(frame);
        input.BackColor=UiTheme.Field; input.ForeColor=UiTheme.Text; input.Font=new Font("Segoe UI",11F);
        input.SetBounds(14,12,width-28,24); input.Anchor=AnchorStyles.Left|AnchorStyles.Right|AnchorStyles.Top;
        TextBox text=input as TextBox; if(text!=null) text.BorderStyle=BorderStyle.None;
        NumericUpDown number=input as NumericUpDown; if(number!=null) { number.BorderStyle=BorderStyle.None; number.Controls[0].Visible=false; }
        input.Enter+=delegate { frame.Invalidate(); }; input.Leave+=delegate { frame.Invalidate(); };
        frame.Click+=delegate { input.Focus(); }; frame.Controls.Add(input);
    }
    LinkLabel FooterLink(string text,int x,int y,int width)
    {
        LinkLabel l=new LinkLabel { Text=text,Left=x,Top=y,Width=width,Height=24,LinkColor=UiTheme.Muted,ActiveLinkColor=UiTheme.Accent,VisitedLinkColor=UiTheme.Muted,LinkBehavior=LinkBehavior.HoverUnderline,BackColor=UiTheme.Background };
        Controls.Add(l); return l;
    }
    void SetupButton(ModernButton b,string text,int x,int y,int width,EventHandler handler,Control parent,int height)
    {
        b.Text=text; b.SetBounds(x,y,width,height); b.Font=new Font("Segoe UI",10F,FontStyle.Bold);
        b.BackColor=parent is SurfacePanel?UiTheme.Surface:UiTheme.Background;
        b.Click+=handler; parent.Controls.Add(b);
    }
    ConnectionSettings GetSettings()
    {
        return new ConnectionSettings { Host = host.Text.Trim(), User = user.Text.Trim(), Port = (int)port.Value,
            KeyPath = ConnectionTools.NormalizeKeyPath(key.Text) };
    }
    void ChooseKey()
    {
        using (OpenFileDialog dialog = new OpenFileDialog())
        {
            dialog.Title = "Выберите приватный SSH-ключ (не .pub)"; dialog.Filter = "Все файлы (*.*)|*.*";
            dialog.CheckFileExists = true;
            string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".ssh");
            if (Directory.Exists(dir)) dialog.InitialDirectory = dir;
            if (dialog.ShowDialog(this) == DialogResult.OK) key.Text = dialog.FileName;
        }
    }
    bool SaveSettings()
    {
        ConnectionSettings s = GetSettings(); string error = ConnectionTools.Validate(s);
        if (error != null) { MessageBox.Show(this, error, "Проверьте настройки"); return false; }
        try { ConnectionTools.Save(SettingsPath, s); status.Text = "Настройки сохранены на этом компьютере."; return true; }
        catch (Exception e) { MessageBox.Show(this, e.Message, "Не удалось сохранить настройки"); return false; }
    }
    void SetBusy(bool value)
    {
        host.Enabled = user.Enabled = key.Enabled = port.Enabled = browse.Enabled = save.Enabled = connect.Enabled = !value;
        disconnect.Enabled = value;
        open.Enabled = connected;
        stateTitle.Text=connected ? "Панель подключена" : connecting ? "Подключаемся к серверу…" : "Готово к подключению";
        stateTitle.ForeColor=connected ? UiTheme.Accent : UiTheme.Text;
        stateDot.DotColor=connected ? UiTheme.Accent : connecting ? Color.FromArgb(255,209,102) : UiTheme.Muted;
        stateDot.Invalidate();
    }
    string SshPath()
    {
        string windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        string ssh = Path.Combine(windows, @"Sysnative\OpenSSH\ssh.exe");
        if (File.Exists(ssh)) return ssh;
        ssh = Path.Combine(windows, @"System32\OpenSSH\ssh.exe");
        if (!File.Exists(ssh)) throw new IOException("Не найден OpenSSH Client Windows. Установите компонент «Клиент OpenSSH» в дополнительных компонентах Windows.");
        return ssh;
    }
    static bool LocalPortFree()
    {
        TcpListener listener = new TcpListener(IPAddress.Loopback, 8787);
        try { listener.Server.ExclusiveAddressUse = true; listener.Start(); return true; }
        catch (SocketException) { return false; }
        finally { listener.Stop(); }
    }
    void AppendLog(string line)
    {
        if (line == null) return;
        lock (logLock) { log.AppendLine(line); if (log.Length > 32000) log.Remove(0, log.Length - 24000); }
    }
    string GetLog() { lock (logLock) return log.ToString(); }
    static bool PanelReady()
    {
        try
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(PanelUrl);
            request.Proxy = null; request.Timeout = 1000; request.ReadWriteTimeout = 1000; request.AllowAutoRedirect = false;
            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
            using (StreamReader reader = new StreamReader(response.GetResponseStream()))
            {
                char[] data = new char[131072]; int count = reader.ReadBlock(data, 0, data.Length);
                string html = new string(data, 0, count);
                return response.StatusCode == HttpStatusCode.OK && html.Contains("panel-token") && html.Contains("OpenFlux");
            }
        }
        catch { return false; }
    }
    async Task Connect()
    {
        if (connecting || connected || !SaveSettings()) return;
        if (!LocalPortFree())
        {
            MessageBox.Show(this, "Порт 8787 уже занят. Закройте прежнее окно SSH-туннеля или запустите Close-OpenFlux.vbs из старого архива, затем повторите подключение. Другие процессы приложение не останавливает.", "Порт занят");
            return;
        }
        int attempt = ++generation;
        connecting = true; connected = false; SetBusy(true);
        tray.Icon = connectingIcon; tray.Text = "OpenFlux — подключение к панели…";
        lock (logLock) log.Clear();
        ConnectionSettings s = GetSettings();
        status.Text = "Подключение к " + s.Host + "…";
        try
        {
            Process p = new Process();
            p.StartInfo = new ProcessStartInfo(SshPath(), ConnectionTools.Arguments(s)) {
                UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden,
                RedirectStandardError = true, RedirectStandardOutput = true, RedirectStandardInput = true };
            p.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { AppendLog(e.Data); };
            p.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { AppendLog(e.Data); };
            tunnel = p;
            p.Start(); p.StandardInput.Close(); p.BeginErrorReadLine(); p.BeginOutputReadLine();
            Stopwatch elapsed = Stopwatch.StartNew();
            while (elapsed.ElapsedMilliseconds < 20000)
            {
                if (attempt != generation) return;
                if (p.HasExited) { p.WaitForExit(); throw new IOException("SSH завершил работу. " + ErrorHint(GetLog())); }
                bool ready = await Task.Run(() => PanelReady());
                if (attempt != generation) return;
                if (ready && !p.HasExited)
                {
                    connecting = false; connected = true; SetBusy(true);
                    status.Text = "Подключено: " + s.User + "@" + s.Host + ". Панель доступна в браузере.";
                    tray.Icon = onlineIcon; tray.Text = "OpenFlux — панель подключена"; OpenPanel(); return;
                }
                await Task.Delay(300);
            }
            throw new IOException("SSH запущен, но панель не ответила за 20 секунд. Проверьте openflux-panel на выбранном VPS: она должна слушать 127.0.0.1:8787.");
        }
        catch (Exception e)
        {
            if (attempt != generation) return;
            Disconnect(); status.Text = "Подключение не установлено.";
            MessageBox.Show(this, e.Message + "\n\nПодробности: «Журнал SSH».", "OpenFlux", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }
    static string ErrorHint(string text)
    {
        if (text.Contains("REMOTE HOST IDENTIFICATION HAS CHANGED")) return "Изменился ключ сервера. Проверьте VPS и его SSH-отпечаток. Приложение не заменяет сохранённый ключ автоматически.";
        if (text.Contains("Host key verification failed")) return "Сервер ещё не подтверждён или его ключ изменился. Откройте «Первое подключение / помощь».";
        if (text.Contains("Permission denied")) return "Сервер не принял ключ. Проверьте пользователя и файл ключа. Если ключ защищён паролем, заранее добавьте его в ssh-agent.";
        return "Проверьте адрес, SSH-порт, доступность VPS и выбранный ключ.";
    }
    void Disconnect()
    {
        ++generation; connecting = connected = false;
        Process p = tunnel; tunnel = null;
        if (p != null)
        {
            try { if (!p.HasExited) { p.Kill(); p.WaitForExit(1500); } } catch { }
            p.Dispose();
        }
        SetBusy(false); status.Text = "Туннель отключён. VPN на сервере продолжает работать."; tray.Icon = offlineIcon; tray.Text = "OpenFlux — панель отключена";
    }
    void Tick(object sender, EventArgs e)
    {
        if (showRequest != null && showRequest.WaitOne(0)) Restore();
        if (connected && tunnel != null && tunnel.HasExited)
        {
            Disconnect(); status.Text = "SSH-соединение потеряно. Нажмите «Подключиться» повторно.";
            tray.ShowBalloonTip(5000, "OpenFlux", "Соединение с панелью потеряно. Откройте настройки и подключитесь снова.", ToolTipIcon.Warning);
        }
    }
    void OpenPanel()
    {
        if (!connected) { Restore(); return; }
        try { Process.Start(new ProcessStartInfo(PanelUrl) { UseShellExecute = true }); }
        catch (Exception e) { MessageBox.Show(this, "Откройте в браузере " + PanelUrl + "\n" + e.Message); }
    }
    void Restore() { Show(); WindowState = FormWindowState.Normal; Activate(); }
    void OnClosing(object sender, FormClosingEventArgs e)
    {
        if (!exiting && e.CloseReason == CloseReason.UserClosing && (connecting || connected))
        {
            e.Cancel = true; Hide();
            tray.ShowBalloonTip(3000, "OpenFlux", "Подключение работает в фоне. Настройки и выход — через значок рядом с часами.", ToolTipIcon.Info); return;
        }
        timer.Stop(); Disconnect(); tray.Visible = false; tray.Dispose();
    }
    void ShowLog()
    {
        using (Form f = new Form { Text = "OpenFlux — журнал SSH", Icon = mainIcon, Width = 760, Height = 430, StartPosition = FormStartPosition.CenterParent })
        {
            f.Controls.Add(new TextBox { Multiline = true, ReadOnly = true, Dock = DockStyle.Fill, ScrollBars = ScrollBars.Both,
                BackColor=UiTheme.Field, ForeColor=UiTheme.Text, BorderStyle=BorderStyle.None, Font = new Font(FontFamily.GenericMonospace, 10), Text = String.IsNullOrWhiteSpace(GetLog()) ? "Сообщений SSH пока нет." : GetLog() });
            f.ShowDialog(this);
        }
    }
    void ShowHelp()
    {
        ConnectionSettings s = GetSettings();
        string command = "ssh -i " + PsQuote(s.KeyPath) + " -p " + s.Port + " -l " + PsQuote(s.User) + " " + PsQuote(s.Host);
        using (Form f = new Form { Text = "OpenFlux — первое подключение", Icon = mainIcon, Width = 770, Height = 490, StartPosition = FormStartPosition.CenterParent })
        {
            f.Controls.Add(new TextBox { Multiline = true, ReadOnly = true, Dock = DockStyle.Fill, ScrollBars = ScrollBars.Vertical,
                BackColor=UiTheme.Field, ForeColor=UiTheme.Text, BorderStyle=BorderStyle.None, Font = new Font("Segoe UI", 10), Text =
                "При первом доступе к новому серверу SSH требует подтвердить его подлинность.\r\n\r\n" +
                "1. Один раз откройте PowerShell на компьютере и выполните:\r\n\r\n" + command + "\r\n\r\n" +
                "2. Сверьте показанный SHA256-отпечаток SSH-сервера с данными провайдера или консоли VPS. Подтверждайте yes только при совпадении. Если ключ изменился, сначала выясните причину.\r\n\r\n" +
                "3. После входа выполните exit, закройте PowerShell и нажмите «Подключиться» в приложении. Постоянно открытая консоль не нужна.\r\n\r\n" +
                "Если ключ защищён паролем, он должен быть предварительно загружен в Windows ssh-agent. Приложение не хранит пароль и не снимает защиту ключа.\r\n\r\n" +
                "На новом VPS нужно отдельно установить панель OpenFlux. Смена адреса не переносит VPN-профили, документы и ключи сервера." });
            f.ShowDialog(this);
        }
    }
    static string PsQuote(string s) { return "'" + s.Replace("'", "''") + "'"; }
}

public static class Program
{
    [STAThread]
    public static void Main(string[] args)
    {
        bool fresh;
        using (Mutex mutex = new Mutex(true, @"Local\OpenFluxPanelLauncherV2", out fresh))
        using (EventWaitHandle signal = new EventWaitHandle(false, EventResetMode.AutoReset, @"Local\OpenFluxPanelLauncherShowV2"))
        {
            if (!fresh) { signal.Set(); return; }
            try
            {
                Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new OpenFluxWindow(signal));
            }
            finally { mutex.ReleaseMutex(); }
        }
    }
}
