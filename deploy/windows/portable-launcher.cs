using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Security;
using System.Text;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("文枢绿色版")]
[assembly: AssemblyDescription("解压即用的文枢启动器")]
[assembly: AssemblyCompany("文枢")]
[assembly: AssemblyProduct("文枢")]
[assembly: AssemblyVersion("__APP_VERSION__")]
[assembly: AssemblyFileVersion("__APP_VERSION__")]

internal static class PortableLauncher
{
    private const string RuntimeKey = @"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}";

    // ProcessStartInfo.Arguments uses Windows command-line quoting, not shell
    // escaping. In particular, backslashes preceding a quote must be doubled.
    internal static string QuoteArgument(string value)
    {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') result.Append('\\', slashes * 2 + 1);
            else result.Append('\\', slashes);
            result.Append(c);
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        return result.Append('"').ToString();
    }

    private static bool RuntimeInstalled()
    {
        foreach (var hive in new[] { RegistryHive.CurrentUser, RegistryHive.LocalMachine })
        foreach (var view in new[] { RegistryView.Registry32, RegistryView.Registry64 })
        {
            try
            {
                using (var registry = RegistryKey.OpenBaseKey(hive, view))
                using (var key = registry.OpenSubKey(RuntimeKey, false))
                {
                    Version version;
                    var text = key == null ? null : key.GetValue("pv") as string;
                    if (Version.TryParse(text, out version) && version.Build >= 0 && version.Revision >= 0 &&
                        version > new Version(0, 0, 0, 0)) return true;
                }
            }
            catch (UnauthorizedAccessException) { }
            catch (SecurityException) { }
            catch (IOException) { }
        }
        return false;
    }

    private static void RequireWritable(string path)
    {
        Directory.CreateDirectory(path);
        var probe = Path.Combine(path, ".wenshu-write-check-" + Guid.NewGuid().ToString("N"));
        using (var file = new FileStream(probe, FileMode.CreateNew, FileAccess.Write,
                                         FileShare.None, 1, FileOptions.DeleteOnClose))
        { file.WriteByte(0); }
    }

    [STAThread]
    private static int Main(string[] arguments)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string app = Path.Combine(root, "app");
        string executable = Path.Combine(app, "Wenshu.exe");
        try
        {
            foreach (var required in new[] { "Wenshu.exe", "WenshuService.exe", "Wenshu.exe.config",
                                             @"_internal\frontend\index.html", @"_internal\presets\manifest.json" })
                if (!File.Exists(Path.Combine(app, required)))
                    throw new FileNotFoundException("请先完整解压绿色版，保留 app 文件夹，再双击根目录的 Wenshu.exe。");

            string data = Path.Combine(root, "data");
            string profile = Path.Combine(root, "profile");
            string temp = Path.Combine(profile, "temp");
            RequireWritable(data);
            RequireWritable(profile);
            RequireWritable(temp);

            if (!RuntimeInstalled())
            {
                string bootstrapper = Path.Combine(root, @"tools\MicrosoftEdgeWebview2Setup.exe");
                string explanation = "未找到 Microsoft Edge WebView2 运行库。\n\n文枢绿色版无需安装，但显示窗口仍需要此运行库。" +
                    "点击“确定”打开随包的微软补装工具，完成后再启动文枢。补装需要联网及系统允许安装运行库。\n\n" +
                    "如果本机禁止安装，请取消并联系电脑管理员。";
                if (MessageBox.Show(explanation, "文枢绿色版", MessageBoxButtons.OKCancel,
                                    MessageBoxIcon.Information) == DialogResult.OK)
                {
                    if (!File.Exists(bootstrapper)) throw new FileNotFoundException("运行库补装工具缺失，请重新完整解压绿色版。");
                    Process.Start(new ProcessStartInfo(bootstrapper) { UseShellExecute = true, WorkingDirectory = root });
                }
                return 0;
            }

            var command = new StringBuilder();
            foreach (var value in new[] { "--profile-dir", profile, "--data-dir", data })
                command.Append(QuoteArgument(value)).Append(' ');
            foreach (var value in arguments) command.Append(QuoteArgument(value)).Append(' ');
            var start = new ProcessStartInfo(executable, command.ToString()) {
                UseShellExecute = false, WorkingDirectory = app, CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Normal
            };
            // Child-only environment: do not change user/system settings.
            foreach (var name in new[] { "TEMP", "TMP", "TMPDIR" }) start.EnvironmentVariables[name] = temp;
            using (var child = Process.Start(start)) { }
            return 0;
        }
        catch (UnauthorizedAccessException)
        {
            MessageBox.Show("当前解压目录拒绝写入。请把整个文枢文件夹移到有写权限的位置，例如 D:\\文枢，再启动。\n\n" +
                            "请先解压，不要从压缩包内直接运行；无需以管理员身份启动。",
                            "文枢绿色版", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        catch (Exception error)
        {
            MessageBox.Show("文枢未能启动。\n\n" + error.Message +
                            "\n\n请确认已完整解压；如已生成日志，请查看 profile\\logs 文件夹。",
                            "文枢绿色版", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        return 1;
    }
}
