// Owns one execution tree. No breakaway flags: ordinary background children stay in the job.
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

class JobHost {
    [StructLayout(LayoutKind.Sequential)] struct BasicLimits {
        public long ProcessTime, JobTime; public uint Flags;
        public UIntPtr MinWorkingSet, MaxWorkingSet; public uint ActiveProcesses;
        public UIntPtr Affinity; public uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] struct IoCounters {
        public ulong ReadOperations, WriteOperations, OtherOperations, ReadBytes, WriteBytes, OtherBytes;
    }
    [StructLayout(LayoutKind.Sequential)] struct ExtendedLimits {
        public BasicLimits Basic; public IoCounters Io;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct StartupInfo {
        public int Size; public string Reserved, Desktop, Title;
        public uint X, Y, XSize, YSize, XChars, YChars, Fill, Flags;
        public ushort ShowWindow, ReservedSize; public IntPtr ReservedPointer, Input, Output, Error;
    }
    [StructLayout(LayoutKind.Sequential)] struct ProcessInfo {
        public IntPtr Process, Thread; public uint Pid, Tid;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool SetInformationJobObject(IntPtr job, int kind, ref ExtendedLimits value, uint length);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool CreateProcess(string application, StringBuilder command, IntPtr processAttributes, IntPtr threadAttributes, bool inherit, uint flags, IntPtr environment, string cwd, ref StartupInfo startup, out ProcessInfo info);
    [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int kind);
    [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll")] static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll")] static extern uint WaitForMultipleObjects(uint count, IntPtr[] handles, bool all, uint timeout);
    [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle, uint timeout);
    [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr process, out uint code);
    [DllImport("kernel32.dll")] static extern bool TerminateProcess(IntPtr process, uint code);
    [DllImport("kernel32.dll")] static extern bool TerminateJobObject(IntPtr job, uint code);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);

    static string Quote(string argument) {
        var result = new StringBuilder("\""); int slashes = 0;
        foreach (char ch in argument) {
            if (ch == '\\') { slashes++; continue; }
            result.Append('\\', ch == '"' ? slashes * 2 + 1 : slashes);
            result.Append(ch); slashes = 0;
        }
        result.Append('\\', slashes * 2); return result.Append('"').ToString();
    }
    static void Require(bool success) { if (!success) throw new Win32Exception(Marshal.GetLastWin32Error()); }

    static int Main(string[] args) {
        IntPtr parent = IntPtr.Zero, job = IntPtr.Zero; ProcessInfo child = new ProcessInfo();
        bool ownsJob = false;
        try {
            if (args.Length < 2) throw new ArgumentException("Expected owner PID and executable");
            parent = OpenProcess(0x00100000, false, int.Parse(args[0]));
            if (parent == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
            job = CreateJobObject(IntPtr.Zero, null);
            if (job == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
            var limits = new ExtendedLimits(); limits.Basic.Flags = 0x00002000; // KILL_ON_JOB_CLOSE
            Require(SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(limits)));
            // Join before creating any child: children inherit job membership atomically.
            // Killing this host during CreateProcess must not strand a suspended child.
            using (var current = System.Diagnostics.Process.GetCurrentProcess())
                Require(AssignProcessToJobObject(job, current.Handle));
            ownsJob = true;
            var command = new StringBuilder();
            for (int index = 1; index < args.Length; index++) { if (index > 1) command.Append(' '); command.Append(Quote(args[index])); }
            var startup = new StartupInfo(); startup.Size = Marshal.SizeOf(startup); startup.Flags = 0x00000100;
            startup.Input = GetStdHandle(-10); startup.Output = GetStdHandle(-11); startup.Error = GetStdHandle(-12);
            Require(CreateProcess(args[1], command, IntPtr.Zero, IntPtr.Zero, true, 0x08000004, IntPtr.Zero, null, ref startup, out child));
            if (ResumeThread(child.Thread) == 0xffffffff) throw new Win32Exception(Marshal.GetLastWin32Error());
            uint wait = WaitForMultipleObjects(2, new IntPtr[] { child.Process, parent }, false, 0xffffffff);
            if (wait == 1) { TerminateJobObject(job, 130); WaitForSingleObject(child.Process, 5000); return 130; }
            if (wait != 0) throw new Win32Exception(Marshal.GetLastWin32Error());
            uint exit; Require(GetExitCodeProcess(child.Process, out exit)); return unchecked((int)exit);
        } catch (Exception exception) {
            Console.Error.WriteLine("Agent Guild process host: " + exception.Message); return 125;
        } finally {
            // A member closing its own last job handle would kill itself before returning
            // the worker exit code. Keep this non-inheritable handle until process exit;
            // Windows then closes it and terminates any surviving descendants.
            if (job != IntPtr.Zero && !ownsJob) CloseHandle(job);
            if (child.Thread != IntPtr.Zero) CloseHandle(child.Thread);
            if (child.Process != IntPtr.Zero) CloseHandle(child.Process);
            if (parent != IntPtr.Zero) CloseHandle(parent);
        }
    }
}
