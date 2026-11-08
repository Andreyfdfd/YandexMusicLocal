using System.Diagnostics;
using System.IO.Compression;
using System.Net;
using System.Net.Http.Headers;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.Win32;

namespace LocalMusicSetup;

internal static class Program
{
    internal const string Owner = "Andreyfdfd";
    internal const string Repository = "YandexMusicLocal";
    internal const string PackageAssetName = "LocalMusic-Package.zip";
    internal const string InstallMarkerName = "INSTALL_INFO.json";
    internal const string UninstallRegistryPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\LocalMusic";

    internal static string Version =>
        Assembly.GetExecutingAssembly().GetName().Version?.ToString(3) ?? "1.0.0";

    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Contains("--self-test", StringComparer.OrdinalIgnoreCase))
            return SelfTest();

        Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
        ApplicationConfiguration.Initialize();

        string executableName = Path.GetFileName(Environment.ProcessPath ?? "");
        if (executableName.Equals("Uninstall.exe", StringComparison.OrdinalIgnoreCase))
            Application.Run(new UninstallForm());
        else
            Application.Run(new SetupForm());

        return 0;
    }

    private static int SelfTest()
    {
        try
        {
            using Stream stream = OpenEmbeddedPackage();
            using var archive = new ZipArchive(stream, ZipArchiveMode.Read);
            RequireEntry(archive, "YandexMusicLocal.exe");
            RequireEntry(archive, "Uninstall.exe");
            RequireEntry(archive, "vlc/libvlc.dll");
            if (!archive.Entries.Any(x => x.FullName.StartsWith("_internal/", StringComparison.OrdinalIgnoreCase)))
                throw new InvalidDataException("PyInstaller runtime files are missing from the payload.");
            Console.WriteLine("Setup self-test passed. Embedded application payload is readable.");
            return 0;
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine(exception);
            return 1;
        }
    }

    internal static Stream OpenEmbeddedPackage()
    {
        return Assembly.GetExecutingAssembly().GetManifestResourceStream("LocalMusic.Package.zip")
            ?? throw new FileNotFoundException("The application package was not embedded into Setup.exe.");
    }

    internal static void ValidatePackage(string zipPath)
    {
        using var archive = ZipFile.OpenRead(zipPath);
        RequireEntry(archive, "YandexMusicLocal.exe");
        RequireEntry(archive, "Uninstall.exe");
        RequireEntry(archive, "vlc/libvlc.dll");
        if (!archive.Entries.Any(x => x.FullName.StartsWith("_internal/", StringComparison.OrdinalIgnoreCase)))
            throw new InvalidDataException("The application runtime folder _internal is missing.");
    }

    private static void RequireEntry(ZipArchive archive, string path)
    {
        if (archive.GetEntry(path) is null)
            throw new InvalidDataException("The application package is incomplete: " + path);
    }

    internal static async Task<string> PreparePackageAsync(
        string downloadDirectory,
        Action<string> reportStatus)
    {
        Directory.CreateDirectory(downloadDirectory);
        string zipPath = Path.Combine(downloadDirectory, PackageAssetName);

        try
        {
            reportStatus("Проверяю последнюю версию в GitHub Releases…");
            await DownloadLatestReleaseAsync(zipPath, reportStatus);
            ValidatePackage(zipPath);
            reportStatus("Последний пакет загружен и проверен.");
            return zipPath;
        }
        catch (Exception exception)
        {
            reportStatus("GitHub Release недоступен напрямую (в том числе из-за приватности репозитория). Использую пакет, встроенный в Setup.exe…");
            try
            {
                using Stream embedded = OpenEmbeddedPackage();
                using var output = new FileStream(zipPath, FileMode.Create, FileAccess.Write, FileShare.None);
                await embedded.CopyToAsync(output);
                output.Close();
                ValidatePackage(zipPath);
                return zipPath;
            }
            catch (Exception embeddedError)
            {
                throw new InvalidOperationException(
                    "Не удалось получить пакет ни из GitHub Release, ни из встроенной копии.\n\n" +
                    "GitHub: " + exception.Message + "\n\n" +
                    "Встроенный пакет: " + embeddedError.Message,
                    embeddedError);
            }
        }
    }

    private static async Task DownloadLatestReleaseAsync(string destination, Action<string> reportStatus)
    {
        using var client = new HttpClient { Timeout = TimeSpan.FromMinutes(5) };
        client.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("LocalMusicSetup", Version));
        client.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));

        string apiUrl = "https://api.github.com/repos/" + Owner + "/" + Repository + "/releases/latest";
        using HttpResponseMessage releaseResponse = await client.GetAsync(apiUrl);
        if (releaseResponse.StatusCode == HttpStatusCode.NotFound ||
            releaseResponse.StatusCode == HttpStatusCode.Forbidden ||
            releaseResponse.StatusCode == HttpStatusCode.Unauthorized)
        {
            throw new InvalidOperationException(
                "GitHub не разрешил анонимное чтение Releases. Для private-репозитория это ожидаемо.");
        }
        releaseResponse.EnsureSuccessStatusCode();

        using JsonDocument document = JsonDocument.Parse(await releaseResponse.Content.ReadAsStringAsync());
        JsonElement assets = document.RootElement.GetProperty("assets");
        JsonElement selected = default;
        foreach (JsonElement asset in assets.EnumerateArray())
        {
            if (asset.TryGetProperty("name", out JsonElement name) &&
                string.Equals(name.GetString(), PackageAssetName, StringComparison.OrdinalIgnoreCase))
            {
                selected = asset;
                break;
            }
        }
        if (selected.ValueKind == JsonValueKind.Undefined)
            throw new FileNotFoundException("В последнем GitHub Release не найден " + PackageAssetName + ".");

        string downloadUrl = selected.GetProperty("browser_download_url").GetString()
            ?? throw new InvalidDataException("У release asset отсутствует адрес загрузки.");
        string? digest = selected.TryGetProperty("digest", out JsonElement digestElement)
            ? digestElement.GetString()
            : null;

        reportStatus("Скачиваю последнюю версию Local Music из GitHub…");
        using HttpResponseMessage packageResponse = await client.GetAsync(
            downloadUrl, HttpCompletionOption.ResponseHeadersRead);
        packageResponse.EnsureSuccessStatusCode();

        await using (Stream source = await packageResponse.Content.ReadAsStreamAsync())
        await using (var target = new FileStream(destination, FileMode.Create, FileAccess.Write, FileShare.None))
        {
            await source.CopyToAsync(target);
        }

        if (!string.IsNullOrWhiteSpace(digest) && digest.StartsWith("sha256:", StringComparison.OrdinalIgnoreCase))
        {
            string actual;
            await using (var stream = File.OpenRead(destination))
            {
                byte[] hash = await SHA256.HashDataAsync(stream);
                actual = Convert.ToHexString(hash).ToLowerInvariant();
            }
            string expected = digest.Substring("sha256:".Length).Trim().ToLowerInvariant();
            if (!CryptographicOperations.FixedTimeEquals(
                Encoding.ASCII.GetBytes(actual), Encoding.ASCII.GetBytes(expected)))
                throw new InvalidDataException("SHA-256 загруженного пакета не совпал с GitHub Release.");
        }
    }

    internal static void CopyDirectory(string source, string destination)
    {
        Directory.CreateDirectory(destination);
        foreach (string file in Directory.EnumerateFiles(source))
        {
            string target = Path.Combine(destination, Path.GetFileName(file));
            File.Copy(file, target, overwrite: true);
        }
        foreach (string directory in Directory.EnumerateDirectories(source))
        {
            string target = Path.Combine(destination, Path.GetFileName(directory));
            CopyDirectory(directory, target);
        }
    }

    internal static bool IsSameOrChildPath(string candidate, string parent)
    {
        string fullCandidate = Path.TrimEndingDirectorySeparator(Path.GetFullPath(candidate));
        string fullParent = Path.TrimEndingDirectorySeparator(Path.GetFullPath(parent));
        if (string.Equals(fullCandidate, fullParent, StringComparison.OrdinalIgnoreCase))
            return true;
        return fullCandidate.StartsWith(fullParent + Path.DirectorySeparatorChar,
            StringComparison.OrdinalIgnoreCase);
    }

    internal static bool IsApplicationRunning()
    {
        return Process.GetProcessesByName("YandexMusicLocal").Any();
    }

    internal static void CreateShortcut(string shortcutPath, string targetPath, string description)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(shortcutPath)!);
        Type shellType = Type.GetTypeFromProgID("WScript.Shell")
            ?? throw new InvalidOperationException("Windows Script Host is unavailable.");
        object shell = Activator.CreateInstance(shellType)
            ?? throw new InvalidOperationException("Could not create Windows shortcut service.");
        object? shortcut = null;
        try
        {
            dynamic automation = shell;
            shortcut = automation.CreateShortcut(shortcutPath);
            dynamic link = shortcut;
            link.TargetPath = targetPath;
            link.Arguments = "";
            link.WorkingDirectory = Path.GetDirectoryName(targetPath);
            link.IconLocation = targetPath + ",0";
            link.Description = description;
            link.Save();
        }
        finally
        {
            if (shortcut is not null && Marshal.IsComObject(shortcut))
                Marshal.FinalReleaseComObject(shortcut);
            if (Marshal.IsComObject(shell))
                Marshal.FinalReleaseComObject(shell);
        }
    }

    internal static string DesktopShortcutPath =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Local Music.lnk");

    internal static string StartMenuShortcutPath =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs", "Local Music", "Local Music.lnk");

    internal static void RegisterInstallation(string installDirectory)
    {
        using RegistryKey key = Registry.CurrentUser.CreateSubKey(UninstallRegistryPath, writable: true)
            ?? throw new InvalidOperationException("Could not create the uninstall registry entry.");
        key.SetValue("DisplayName", "Local Music");
        key.SetValue("DisplayVersion", Version);
        key.SetValue("Publisher", "Andreyfdfd");
        key.SetValue("InstallLocation", installDirectory);
        key.SetValue("DisplayIcon", Path.Combine(installDirectory, "YandexMusicLocal.exe"));
        key.SetValue("UninstallString", "\"" + Path.Combine(installDirectory, "Uninstall.exe") + "\"");
        key.SetValue("NoModify", 1, RegistryValueKind.DWord);
        key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
    }
}

internal sealed class SetupForm : Form
{
    private readonly TextBox _installPath;
    private readonly TextBox _downloadPath;
    private readonly CheckBox _desktopShortcut;
    private readonly CheckBox _startMenuShortcut;
    private readonly CheckBox _launchAfterInstall;
    private readonly Button _installButton;
    private readonly Label _status;
    private readonly ProgressBar _progress;

    public SetupForm()
    {
        Text = "Установка Local Music";
        StartPosition = FormStartPosition.CenterScreen;
        MinimumSize = new Size(720, 470);
        Size = new Size(760, 520);
        Font = new Font("Segoe UI", 9F);
        BackColor = Color.FromArgb(24, 24, 28);
        ForeColor = Color.White;

        var root = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            Padding = new Padding(22),
            ColumnCount = 1,
            RowCount = 7,
            BackColor = BackColor
        };
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 64));
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 74));
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 74));
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 92));
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 24));
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 24));
        root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        Controls.Add(root);

        var heading = new Label
        {
            Text = "Local Music",
            Font = new Font("Segoe UI Semibold", 22F, FontStyle.Bold),
            ForeColor = Color.FromArgb(255, 223, 0),
            Dock = DockStyle.Fill,
            TextAlign = ContentAlignment.MiddleLeft
        };
        root.Controls.Add(heading, 0, 0);

        _installPath = AddFolderRow(root, 1, "Папка установки программы",
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Local Music"));
        _downloadPath = AddFolderRow(root, 2, "Временная папка загрузки и распаковки",
            Path.Combine(Path.GetTempPath(), "LocalMusic-Setup"));

        var options = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill,
            FlowDirection = FlowDirection.TopDown,
            WrapContents = false,
            Padding = new Padding(0, 8, 0, 0),
            BackColor = BackColor
        };
        _desktopShortcut = NewCheckBox("Создать ярлык на рабочем столе", true);
        _startMenuShortcut = NewCheckBox("Добавить Local Music в меню «Пуск»", true);
        _launchAfterInstall = NewCheckBox("Запустить Local Music после установки", true);
        options.Controls.AddRange([_desktopShortcut, _startMenuShortcut, _launchAfterInstall]);
        root.Controls.Add(options, 0, 3);

        _status = new Label
        {
            Text = "Установщик подготовлен. Нажми «Установить».",
            Dock = DockStyle.Fill,
            ForeColor = Color.Gainsboro,
            TextAlign = ContentAlignment.MiddleLeft
        };
        root.Controls.Add(_status, 0, 4);

        _progress = new ProgressBar { Dock = DockStyle.Fill, Style = ProgressBarStyle.Blocks, Value = 0 };
        root.Controls.Add(_progress, 0, 5);

        var footer = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill,
            FlowDirection = FlowDirection.RightToLeft,
            Padding = new Padding(0, 10, 0, 0),
            BackColor = BackColor
        };
        _installButton = new Button
        {
            Text = "Установить",
            Width = 150,
            Height = 38,
            BackColor = Color.FromArgb(255, 223, 0),
            ForeColor = Color.FromArgb(22, 22, 24),
            FlatStyle = FlatStyle.Flat,
            Font = new Font("Segoe UI Semibold", 10F, FontStyle.Bold)
        };
        _installButton.FlatAppearance.BorderSize = 0;
        _installButton.Click += async (_, _) => await InstallAsync();
        footer.Controls.Add(_installButton);
        var cancel = new Button { Text = "Отмена", Width = 110, Height = 38, DialogResult = DialogResult.Cancel };
        footer.Controls.Add(cancel);
        root.Controls.Add(footer, 0, 6);
        CancelButton = cancel;
    }

    private static CheckBox NewCheckBox(string text, bool isChecked) =>
        new() { Text = text, Checked = isChecked, AutoSize = true, ForeColor = Color.Gainsboro, BackColor = Color.Transparent };

    private static TextBox AddFolderRow(TableLayoutPanel root, int row, string caption, string initialValue)
    {
        var panel = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            ColumnCount = 2,
            RowCount = 2,
            BackColor = root.BackColor,
            Margin = new Padding(0, 4, 0, 4)
        };
        panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        panel.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 112));
        panel.RowStyles.Add(new RowStyle(SizeType.Absolute, 22));
        panel.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        var label = new Label { Text = caption, Dock = DockStyle.Fill, ForeColor = Color.Gainsboro, TextAlign = ContentAlignment.MiddleLeft };
        panel.Controls.Add(label, 0, 0);
        panel.SetColumnSpan(label, 2);
        var box = new TextBox { Text = initialValue, Dock = DockStyle.Fill, ReadOnly = false };
        panel.Controls.Add(box, 0, 1);
        var browse = new Button { Text = "Обзор…", Dock = DockStyle.Fill, Height = 30 };
        browse.Click += (_, _) =>
        {
            using var dialog = new FolderBrowserDialog { Description = caption, UseDescriptionForTitle = true, SelectedPath = box.Text };
            if (dialog.ShowDialog(root.FindForm()) == DialogResult.OK)
                box.Text = dialog.SelectedPath;
        };
        panel.Controls.Add(browse, 1, 1);
        root.Controls.Add(panel, 0, row);
        return box;
    }

    private void ReportStatus(string message)
    {
        if (InvokeRequired)
        {
            BeginInvoke(new Action(() => _status.Text = message));
            return;
        }
        _status.Text = message;
        Application.DoEvents();
    }

    private async Task InstallAsync()
    {
        _installButton.Enabled = false;
        _progress.Style = ProgressBarStyle.Marquee;
        _progress.MarqueeAnimationSpeed = 24;
        string? extractionDirectory = null;
        string? deploymentDirectory = null;
        string? backupDirectory = null;

        try
        {
            if (Program.IsApplicationRunning())
                throw new InvalidOperationException("Сначала полностью закрой Local Music, затем повтори установку.");

            string installDirectory = Path.GetFullPath(_installPath.Text.Trim());
            string downloadDirectory = Path.GetFullPath(_downloadPath.Text.Trim());
            if (string.IsNullOrWhiteSpace(installDirectory) || string.IsNullOrWhiteSpace(downloadDirectory))
                throw new InvalidOperationException("Укажи обе папки.");
            if (string.Equals(installDirectory, Path.GetPathRoot(installDirectory), StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Нельзя устанавливать программу в корень диска.");
            if (Program.IsSameOrChildPath(installDirectory, downloadDirectory) ||
                Program.IsSameOrChildPath(downloadDirectory, installDirectory))
                throw new InvalidOperationException("Папки загрузки и установки должны быть раздельными.");
            Directory.CreateDirectory(downloadDirectory);
            Directory.CreateDirectory(Path.GetDirectoryName(installDirectory)!);

            ReportStatus("Получаю установочный пакет…");
            string zipPath = await Program.PreparePackageAsync(downloadDirectory, ReportStatus);

            extractionDirectory = Path.Combine(downloadDirectory, "LocalMusic-Extract-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(extractionDirectory);
            ReportStatus("Распаковываю программу и аудиодвижок VLC…");
            ZipFile.ExtractToDirectory(zipPath, extractionDirectory, overwriteFiles: true);
            string appExe = Path.Combine(extractionDirectory, "YandexMusicLocal.exe");
            if (!File.Exists(appExe) || !Directory.Exists(Path.Combine(extractionDirectory, "_internal")) ||
                !File.Exists(Path.Combine(extractionDirectory, "vlc", "libvlc.dll")) ||
                !File.Exists(Path.Combine(extractionDirectory, "Uninstall.exe")))
                throw new InvalidDataException("В пакете не хватает файлов приложения, VLC или деинсталлятора.");

            deploymentDirectory = installDirectory + ".install-staging-" + Guid.NewGuid().ToString("N");
            ReportStatus("Подготавливаю папку программы…");
            Program.CopyDirectory(extractionDirectory, deploymentDirectory);
            File.WriteAllText(
                Path.Combine(deploymentDirectory, Program.InstallMarkerName),
                JsonSerializer.Serialize(new
                {
                    Name = "Local Music",
                    Version = Program.Version,
                    InstalledAtUtc = DateTime.UtcNow,
                    InstallDirectory = installDirectory
                }, new JsonSerializerOptions { WriteIndented = true }),
                new UTF8Encoding(false));

            if (Directory.Exists(installDirectory))
            {
                if (!File.Exists(Path.Combine(installDirectory, Program.InstallMarkerName)))
                    throw new IOException("Выбранная папка уже существует и не распознана как установка Local Music. Выбери пустую папку.");
                backupDirectory = installDirectory + ".backup-" + DateTime.Now.ToString("yyyyMMdd-HHmmss");
                Directory.Move(installDirectory, backupDirectory);
            }

            try
            {
                Directory.Move(deploymentDirectory, installDirectory);
                deploymentDirectory = null;
            }
            catch
            {
                if (backupDirectory is not null && Directory.Exists(backupDirectory) && !Directory.Exists(installDirectory))
                    Directory.Move(backupDirectory, installDirectory);
                throw;
            }

            ReportStatus("Создаю ярлыки и запись удаления…");
            string installedExe = Path.Combine(installDirectory, "YandexMusicLocal.exe");
            var shortcutWarnings = new List<string>();
            if (_desktopShortcut.Checked)
            {
                try { Program.CreateShortcut(Program.DesktopShortcutPath, installedExe, "Локальный музыкальный плеер Local Music"); }
                catch (Exception exception) { shortcutWarnings.Add("Рабочий стол: " + exception.Message); }
            }
            if (_startMenuShortcut.Checked)
            {
                try { Program.CreateShortcut(Program.StartMenuShortcutPath, installedExe, "Локальный музыкальный плеер Local Music"); }
                catch (Exception exception) { shortcutWarnings.Add("Меню «Пуск»: " + exception.Message); }
            }

            try { Program.RegisterInstallation(installDirectory); }
            catch (Exception exception) { shortcutWarnings.Add("Запись удаления Windows: " + exception.Message); }

            if (backupDirectory is not null && Directory.Exists(backupDirectory))
                Directory.Delete(backupDirectory, recursive: true);

            try { File.Delete(zipPath); } catch { /* temporary download cleanup is best-effort */ }
            try { if (extractionDirectory is not null && Directory.Exists(extractionDirectory)) Directory.Delete(extractionDirectory, true); } catch { }

            _progress.Style = ProgressBarStyle.Blocks;
            _progress.Value = 100;
            ReportStatus("Установка Local Music завершена.");
            string detail = "Программа установлена.\n\nПапка: " + installDirectory +
                "\nДеинсталлятор: " + Path.Combine(installDirectory, "Uninstall.exe") +
                "\n\nПри первом запуске выбери папку со своей музыкой.";
            if (shortcutWarnings.Count > 0)
                detail += "\n\nПредупреждения:\n" + string.Join("\n", shortcutWarnings);
            bool launch = _launchAfterInstall.Checked &&
                MessageBox.Show(detail + "\n\nЗапустить Local Music сейчас?", "Установка завершена",
                    MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes;

            if (_launchAfterInstall.Checked && !launch && shortcutWarnings.Count == 0)
            {
                // The user chose not to launch.
            }
            Close();
            if (launch)
            {
                Process.Start(new ProcessStartInfo(installedExe)
                {
                    UseShellExecute = true,
                    WorkingDirectory = installDirectory
                });
            }
            else if (!_launchAfterInstall.Checked)
            {
                MessageBox.Show(detail, "Установка завершена", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
        }
        catch (Exception exception)
        {
            _progress.Style = ProgressBarStyle.Blocks;
            _progress.MarqueeAnimationSpeed = 0;
            _progress.Value = 0;
            ReportStatus("Установка не завершена.");
            MessageBox.Show(exception.Message, "Ошибка установки Local Music",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
            _installButton.Enabled = true;
        }
        finally
        {
            try { if (deploymentDirectory is not null && Directory.Exists(deploymentDirectory)) Directory.Delete(deploymentDirectory, true); } catch { }
            try { if (extractionDirectory is not null && Directory.Exists(extractionDirectory)) Directory.Delete(extractionDirectory, true); } catch { }
        }
    }
}

internal sealed class UninstallForm : Form
{
    private readonly CheckBox _removeUserData;

    public UninstallForm()
    {
        Text = "Удаление Local Music";
        StartPosition = FormStartPosition.CenterScreen;
        Size = new Size(540, 300);
        MinimumSize = new Size(540, 300);
        Font = new Font("Segoe UI", 9F);
        BackColor = Color.FromArgb(24, 24, 28);
        ForeColor = Color.White;

        string installDirectory = Path.GetDirectoryName(Environment.ProcessPath ?? Application.ExecutablePath)!;
        bool validInstall = File.Exists(Path.Combine(installDirectory, Program.InstallMarkerName));

        var root = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(20), RowCount = 4, ColumnCount = 1 };
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 48));
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 76));
        root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 48));
        Controls.Add(root);
        root.Controls.Add(new Label
        {
            Text = "Удаление Local Music",
            Font = new Font("Segoe UI Semibold", 17F, FontStyle.Bold),
            ForeColor = Color.FromArgb(255, 223, 0),
            Dock = DockStyle.Fill,
            TextAlign = ContentAlignment.MiddleLeft
        }, 0, 0);
        root.Controls.Add(new Label
        {
            Text = validInstall
                ? "Будут удалены файлы плеера, VLC runtime, ярлыки и запись программы в Windows."
                : "Не найдена метка установки Local Music. Удаление остановлено, чтобы не затронуть чужую папку.",
            Dock = DockStyle.Fill,
            ForeColor = validInstall ? Color.Gainsboro : Color.Salmon,
            TextAlign = ContentAlignment.MiddleLeft
        }, 0, 1);
        _removeUserData = new CheckBox
        {
            Text = "Также удалить настройки, историю прослушивания, индекс и кэш (%LOCALAPPDATA%\\LocalMusic)",
            Checked = false,
            AutoSize = true,
            ForeColor = Color.Gainsboro,
            Dock = DockStyle.Top
        };
        root.Controls.Add(_removeUserData, 0, 2);
        var footer = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.RightToLeft };
        var remove = new Button
        {
            Text = "Удалить программу",
            Width = 160,
            Height = 36,
            Enabled = validInstall,
            BackColor = Color.FromArgb(255, 223, 0),
            ForeColor = Color.Black,
            FlatStyle = FlatStyle.Flat
        };
        remove.Click += (_, _) => BeginUninstall(installDirectory);
        footer.Controls.Add(remove);
        var cancel = new Button { Text = "Отмена", Width = 100, Height = 36, DialogResult = DialogResult.Cancel };
        footer.Controls.Add(cancel);
        root.Controls.Add(footer, 0, 3);
        CancelButton = cancel;
    }

    private void BeginUninstall(string installDirectory)
    {
        if (Program.IsApplicationRunning())
        {
            MessageBox.Show("Сначала закрой Local Music, затем запусти Uninstall.exe ещё раз.",
                "Закройте приложение", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }

        DialogResult answer = MessageBox.Show(
            _removeUserData.Checked
                ? "Удалить Local Music вместе с настройками, историей, индексом и кэшем?\nПапка с твоей музыкой удаляться не будет."
                : "Удалить файлы Local Music?\nНастройки, историю прослушивания и индекс библиотеки оставлю на компьютере.\nПапка с музыкой не затрагивается.",
            "Подтверждение удаления", MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
        if (answer != DialogResult.Yes)
            return;

        try
        {
            string dataDirectory = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LocalMusic");

            TryDeleteFile(Program.DesktopShortcutPath);
            TryDeleteFile(Program.StartMenuShortcutPath);
            TryDeleteDirectory(Path.GetDirectoryName(Program.StartMenuShortcutPath)!);
            Registry.CurrentUser.DeleteSubKeyTree(Program.UninstallRegistryPath, throwOnMissingSubKey: false);

            string scriptPath = Path.Combine(Path.GetTempPath(), "LocalMusic-Uninstall-" + Guid.NewGuid().ToString("N") + ".ps1");
            string script = "$ErrorActionPreference = 'SilentlyContinue'\r\n" +
                "Start-Sleep -Seconds 3\r\n" +
                "Remove-Item -LiteralPath '" + EscapePowerShellLiteral(installDirectory) + "' -Recurse -Force\r\n";
            if (_removeUserData.Checked)
                script += "Remove-Item -LiteralPath '" + EscapePowerShellLiteral(dataDirectory) + "' -Recurse -Force\r\n";
            script += "Remove-Item -LiteralPath $PSCommandPath -Force\r\n";
            File.WriteAllText(scriptPath, script, new UTF8Encoding(false));

            Process.Start(new ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + scriptPath + "\"",
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            }) ?? throw new InvalidOperationException("Не удалось запустить очистку файлов программы.");

            MessageBox.Show(
                "Удаление запущено. Файлы программы будут удалены после закрытия деинсталлятора.\n" +
                (_removeUserData.Checked ? "Пользовательские данные Local Music также будут удалены." :
                    "Пользовательские данные сохранены."),
                "Local Music", MessageBoxButtons.OK, MessageBoxIcon.Information);
            Close();
        }
        catch (Exception exception)
        {
            MessageBox.Show(exception.Message, "Не удалось удалить Local Music",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private static string EscapePowerShellLiteral(string value) => value.Replace("'", "''");

    private static void TryDeleteFile(string path)
    {
        try { if (File.Exists(path)) File.Delete(path); } catch { }
    }

    private static void TryDeleteDirectory(string path)
    {
        try { if (Directory.Exists(path) && !Directory.EnumerateFileSystemEntries(path).Any()) Directory.Delete(path); } catch { }
    }
}
