using Microsoft.Win32;
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows.Forms;

internal static class UninstallProgram
{
    private const string MarkerName = "INSTALL_INFO.json";
    internal const string RegistryPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\LocalMusic";

    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Any(x => string.Equals(x, "--self-test", StringComparison.OrdinalIgnoreCase)))
        {
            Console.WriteLine("Local Music uninstaller self-test passed.");
            return 0;
        }

        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);

        string installDirectory = Path.GetDirectoryName(Application.ExecutablePath);
        if (string.IsNullOrEmpty(installDirectory) || !File.Exists(Path.Combine(installDirectory, MarkerName)))
        {
            MessageBox.Show(
                "Не найдена метка установки Local Music. Деинсталлятор остановлен, чтобы не удалить чужие файлы.",
                "Local Music", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        Application.Run(new UninstallWindow(installDirectory));
        return 0;
    }
}

internal sealed class UninstallWindow : Form
{
    private readonly string _installDirectory;
    private readonly CheckBox _removeUserData;

    internal UninstallWindow(string installDirectory)
    {
        _installDirectory = installDirectory;
        Text = "Удаление Local Music";
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new System.Drawing.Size(560, 285);
        MinimumSize = new System.Drawing.Size(560, 285);
        Font = new System.Drawing.Font("Segoe UI", 9F);
        BackColor = System.Drawing.Color.FromArgb(24, 24, 28);
        ForeColor = System.Drawing.Color.White;

        var heading = new Label
        {
            Text = "Удаление Local Music",
            Font = new System.Drawing.Font("Segoe UI", 17F, System.Drawing.FontStyle.Bold),
            ForeColor = System.Drawing.Color.FromArgb(255, 223, 0),
            Dock = DockStyle.Top,
            Height = 54,
            TextAlign = System.Drawing.ContentAlignment.MiddleLeft
        };
        Controls.Add(heading);

        var description = new Label
        {
            Text = "Будут удалены файлы Local Music, встроенный аудиодвижок VLC, ярлыки и запись программы в Windows.",
            Dock = DockStyle.Top,
            Height = 66,
            TextAlign = System.Drawing.ContentAlignment.MiddleLeft
        };
        Controls.Add(description);

        _removeUserData = new CheckBox
        {
            Text = "Также удалить настройки, историю прослушивания, индекс и кэш (%LOCALAPPDATA%\\LocalMusic)",
            Checked = false,
            AutoSize = true,
            ForeColor = System.Drawing.Color.Gainsboro,
            Location = new System.Drawing.Point(12, 132)
        };
        Controls.Add(_removeUserData);

        var remove = new Button
        {
            Text = "Удалить программу",
            Width = 165,
            Height = 36,
            Location = new System.Drawing.Point(368, 207),
            BackColor = System.Drawing.Color.FromArgb(255, 223, 0),
            ForeColor = System.Drawing.Color.Black,
            FlatStyle = FlatStyle.Flat
        };
        remove.FlatAppearance.BorderSize = 0;
        remove.Click += delegate { BeginUninstall(); };
        Controls.Add(remove);

        var cancel = new Button
        {
            Text = "Отмена",
            Width = 100,
            Height = 36,
            Location = new System.Drawing.Point(258, 207),
            DialogResult = DialogResult.Cancel
        };
        Controls.Add(cancel);
        CancelButton = cancel;
    }

    private void BeginUninstall()
    {
        if (Process.GetProcessesByName("YandexMusicLocal").Any())
        {
            MessageBox.Show(
                "Сначала полностью закрой Local Music, затем запусти Uninstall.exe ещё раз.",
                "Закройте приложение", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }

        string question = _removeUserData.Checked
            ? "Удалить Local Music вместе с настройками, историей, индексом и кэшем?\nПапка с музыкой удаляться не будет."
            : "Удалить файлы Local Music?\nНастройки, историю прослушивания и индекс библиотеки оставлю на компьютере.\nПапка с музыкой не затрагивается.";
        if (MessageBox.Show(question, "Подтверждение удаления",
            MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes)
            return;

        try
        {
            string desktopLink = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Local Music.lnk");
            string startMenuFolder = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs", "Local Music");
            string startMenuLink = Path.Combine(startMenuFolder, "Local Music.lnk");
            string dataDirectory = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LocalMusic");

            TryDeleteFile(desktopLink);
            TryDeleteFile(startMenuLink);
            TryDeleteDirectory(startMenuFolder);
            try { Registry.CurrentUser.DeleteSubKeyTree(RegistryPath, false); } catch { }

            string scriptPath = Path.Combine(
                Path.GetTempPath(), "LocalMusic-Uninstall-" + Guid.NewGuid().ToString("N") + ".ps1");
            var script = new StringBuilder();
            script.AppendLine("$ErrorActionPreference = 'SilentlyContinue'");
            script.AppendLine("Start-Sleep -Seconds 3");
            script.AppendLine("Remove-Item -LiteralPath '" + EscapePowerShell(_installDirectory) + "' -Recurse -Force");
            if (_removeUserData.Checked)
                script.AppendLine("Remove-Item -LiteralPath '" + EscapePowerShell(dataDirectory) + "' -Recurse -Force");
            script.AppendLine("Remove-Item -LiteralPath $PSCommandPath -Force");
            File.WriteAllText(scriptPath, script.ToString(), new UTF8Encoding(false));

            Process.Start(new ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + scriptPath + "\"",
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            });

            MessageBox.Show(
                "Удаление запущено. Файлы программы будут удалены после закрытия деинсталлятора.\n" +
                (_removeUserData.Checked ? "Пользовательские данные Local Music также будут удалены." :
                    "Пользовательские данные Local Music сохранены."),
                "Local Music", MessageBoxButtons.OK, MessageBoxIcon.Information);
            Close();
        }
        catch (Exception exception)
        {
            MessageBox.Show(exception.Message, "Не удалось удалить Local Music",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private static string EscapePowerShell(string value)
    {
        return value.Replace("'", "''");
    }

    private static void TryDeleteFile(string path)
    {
        try { if (File.Exists(path)) File.Delete(path); } catch { }
    }

    private static void TryDeleteDirectory(string path)
    {
        try { if (Directory.Exists(path) && !Directory.EnumerateFileSystemEntries(path).Any()) Directory.Delete(path); } catch { }
    }
}
