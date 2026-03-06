"""Tests for command safety classification."""

from archon.safety import Level, classify


class TestForbidden:
    def test_rm_rf_root(self):
        assert classify("rm -rf /") == Level.FORBIDDEN

    def test_rm_r_root(self):
        assert classify("rm -r /") == Level.FORBIDDEN

    def test_dd_to_block_device(self):
        assert classify("dd if=/dev/zero of=/dev/sda") == Level.FORBIDDEN

    def test_mkfs_block_device(self):
        assert classify("mkfs.ext4 /dev/sda1") == Level.FORBIDDEN

    def test_mkfs_nvme(self):
        assert classify("mkfs /dev/nvme0n1") == Level.FORBIDDEN

    def test_safety_py_edit(self):
        assert classify("vim /tmp/archon/safety.py",
                        archon_source_dir="/tmp/archon") == Level.FORBIDDEN


class TestSafe:
    def test_ls(self):
        assert classify("ls -la") == Level.SAFE

    def test_cat(self):
        assert classify("cat /etc/hostname") == Level.SAFE

    def test_grep(self):
        assert classify("grep -r pattern .") == Level.SAFE

    def test_pacman_query(self):
        assert classify("pacman -Q") == Level.SAFE

    def test_pacman_search(self):
        assert classify("pacman -Ss htop") == Level.SAFE

    def test_systemctl_status(self):
        assert classify("systemctl status nginx") == Level.SAFE

    def test_git_log(self):
        assert classify("git log --oneline -10") == Level.SAFE

    def test_git_status(self):
        assert classify("git status") == Level.SAFE

    def test_docker_ps(self):
        assert classify("docker ps -a") == Level.SAFE

    def test_journalctl(self):
        assert classify("journalctl -u sshd") == Level.SAFE

    def test_pip_list(self):
        assert classify("pip list") == Level.SAFE

    def test_free(self):
        assert classify("free -h") == Level.SAFE

    def test_df(self):
        assert classify("df -h") == Level.SAFE

    def test_sed_read_only(self):
        assert classify("sed -n '1,5p' file.txt") == Level.SAFE


class TestDangerous:
    def test_pacman_install(self):
        assert classify("pacman -S htop") == Level.DANGEROUS

    def test_pacman_syu(self):
        assert classify("pacman -Syu") == Level.DANGEROUS

    def test_pacman_remove(self):
        assert classify("pacman -R package") == Level.DANGEROUS

    def test_systemctl_restart(self):
        assert classify("systemctl restart nginx") == Level.DANGEROUS

    def test_git_push(self):
        assert classify("git push origin main") == Level.DANGEROUS

    def test_sudo(self):
        assert classify("sudo ls") == Level.DANGEROUS

    def test_rm_file(self):
        assert classify("rm important.txt") == Level.DANGEROUS

    def test_kill(self):
        assert classify("kill -9 1234") == Level.DANGEROUS

    def test_docker_run(self):
        assert classify("docker run ubuntu") == Level.DANGEROUS

    def test_pip_install(self):
        assert classify("pip install requests") == Level.DANGEROUS

    def test_unknown_command(self):
        assert classify("some_unknown_binary --flag") == Level.DANGEROUS

    def test_sed_in_place(self):
        assert classify("sed -i 's/a/b/' file.txt") == Level.DANGEROUS

    def test_sed_in_place_combined_flags(self):
        assert classify("sed -Ei 's/a/b/' file.txt") == Level.DANGEROUS


class TestPipes:
    def test_safe_pipe(self):
        assert classify("ps aux | grep python") == Level.SAFE

    def test_dangerous_pipe(self):
        assert classify("echo test | sudo tee /etc/file") == Level.DANGEROUS

    def test_chain_safe(self):
        assert classify("ls && pwd") == Level.SAFE

    def test_chain_dangerous(self):
        assert classify("ls && rm file") == Level.DANGEROUS


class TestSelfModification:
    def test_edit_own_source_dangerous(self):
        assert classify("vim /opt/archon/agent.py",
                        archon_source_dir="/opt/archon") == Level.DANGEROUS

    def test_edit_safety_forbidden(self):
        assert classify("nano /opt/archon/safety.py",
                        archon_source_dir="/opt/archon") == Level.FORBIDDEN
