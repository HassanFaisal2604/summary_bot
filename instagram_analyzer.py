import re
from collections import defaultdict, OrderedDict
from datetime import datetime
from typing import Dict, List, Tuple, Set

class InstagramAutomationAnalyzer:
    """
    Analyzes raw Instagram automation logs for 60-device farm.
    Performs line-by-line summation, identifies errors, and generates Discord reports.
    """
    
    def __init__(self):
        self.phones = {}  # {phone_number: phone_data}
        self.total_follows = 0
        self.total_unfollows = 0
        self.active_phones = 0
        self.failed_accounts = []
        self.excluded_phones = set()
    
    def parse_log(self, raw_text: str) -> None:
        """
        Parse raw log text into structured data.
        Handles multi-line format with phones and accounts.
        Supports both data.txt format and summary bot output format (with '   * ' prefix).
        """
        lines = raw_text.strip().split('\n')
        current_phone = None
        current_method = None
        
        for line in lines:
            # Remove summary bot format prefix if present ('   * ' or '   *')
            # Check BEFORE stripping to preserve the prefix
            original_line = line
            if line.startswith('   * '):
                line_stripped = line[5:].strip()
            elif line.startswith('   *'):
                line_stripped = line[4:].strip()
            else:
                line_stripped = line.strip()
            
            # Match phone header: "Phone X – status"
            phone_match = re.match(r'Phone (\d+)\s*–\s*(.+)', line_stripped)
            if phone_match:
                phone_num = int(phone_match.group(1))
                status = phone_match.group(2).strip()
                
                current_phone = phone_num
                current_method = None
                
                # Extract method if present
                method_match = re.search(r'\(Method (\d+)\)', status)
                if method_match:
                    current_method = int(method_match.group(1))
                
                self.phones[phone_num] = {
                    'number': phone_num,
                    'status': status,
                    'method': current_method,
                    'accounts': [],
                    'no_task_made': 'no daily task made' in status.lower(),
                    'completed': 'completed daily task' in status.lower()
                }
                continue
            
            # Skip empty lines
            if not line_stripped:
                continue
            
            # Match account lines (only if we have a current phone)
            if current_phone is not None:
                # Account with metrics: "account_name - total # of follows made: X (met/didn't met...)"
                follows_match = re.match(
                    r'([a-zA-Z0-9._]+)\s*-\s*total\s*#\s*of\s*follows\s*made:\s*(\d+)',
                    line_stripped,
                    re.IGNORECASE
                )
                if follows_match:
                    account_name = follows_match.group(1)
                    follows_count = int(follows_match.group(2))
                    # Check for "met the daily max" but NOT "didn't met the daily max"
                    met_max = 'met the daily max' in line_stripped and "didn't" not in line_stripped
                    max_match = re.search(r'which is (\d+)', line_stripped)
                    daily_max = int(max_match.group(1)) if max_match else None
                    
                    self.phones[current_phone]['accounts'].append({
                        'name': account_name,
                        'follows': follows_count,
                        'unfollows': 0,
                        'daily_max': daily_max,
                        'met_max': met_max,
                        'status': 'active'
                    })
                    continue
                
                # Unfollows: "account_name - total # of unfollows made: X"
                unfollows_match = re.match(
                    r'([a-zA-Z0-9._]+)\s*-\s*total\s*#\s*of\s*unfollows\s*made:\s*(\d+)',
                    line_stripped,
                    re.IGNORECASE
                )
                if unfollows_match:
                    account_name = unfollows_match.group(1)
                    unfollows_count = int(unfollows_match.group(2))
                    
                    # Find existing account or add new
                    found = False
                    for account in self.phones[current_phone]['accounts']:
                        if account['name'] == account_name:
                            account['unfollows'] = unfollows_count
                            found = True
                            break
                    if not found:
                        self.phones[current_phone]['accounts'].append({
                            'name': account_name,
                            'follows': 0,
                            'unfollows': unfollows_count,
                            'daily_max': None,
                            'met_max': None,
                            'status': 'active'
                        })
                    continue
                
                # Blocked account: "account_name – blocked"
                if '–' in line_stripped and 'blocked' in line_stripped.lower():
                    account_name = line_stripped.split('–')[0].strip()
                    self.phones[current_phone]['accounts'].append({
                        'name': account_name,
                        'follows': 0,
                        'unfollows': 0,
                        'daily_max': None,
                        'met_max': None,
                        'status': 'blocked'
                    })
                    continue
                
                # Offline account: "account_name – off"
                if '–' in line_stripped and line_stripped.endswith('off'):
                    account_name = line_stripped.replace('–', '-').split('-')[0].strip()
                    self.phones[current_phone]['accounts'].append({
                        'name': account_name,
                        'follows': 0,
                        'unfollows': 0,
                        'daily_max': None,
                        'met_max': None,
                        'status': 'off'
                    })
                    continue
                
                # Account name only (Method 9 - warmup/story viewing, no metrics listed)
                if re.match(r'^[a-zA-Z0-9._]+$', line_stripped):
                    # Check if this isn't a header or other pattern
                    if not any(skip in line_stripped for skip in ['Phone', 'Daily', 'Summary']):
                        self.phones[current_phone]['accounts'].append({
                            'name': line_stripped,
                            'follows': 0,
                            'unfollows': 0,
                            'daily_max': None,
                            'met_max': None,
                            'status': 'active'
                        })
    
    def calculate_totals(self) -> None:
        """
        Line-by-line summation for 100% accuracy.
        Sum all follows and unfollows across all active accounts.
        """
        self.total_follows = 0
        self.total_unfollows = 0
        self.active_phones = 0
        
        for phone_num in sorted(self.phones.keys()):
            phone_data = self.phones[phone_num]
            
            # Only count phones with completed tasks or Method 9
            if phone_data['completed']:
                self.active_phones += 1
                
                # Line-by-line sum
                for account in phone_data['accounts']:
                    if account['status'] != 'off':
                        self.total_follows += account['follows']
                        self.total_unfollows += account['unfollows']
    
    def identify_errors(self) -> None:
        """
        Categorize failures into: Configuration, Targeting, System, Performance.
        Only report phones that completed tasks but had errors.
        Skip "no daily task made" phones.
        """
        self.failed_accounts = []
        
        for phone_num in sorted(self.phones.keys()):
            # Skip excluded phones (21-25 or user-specified)
            if phone_num in self.excluded_phones:
                continue
            
            phone_data = self.phones[phone_num]
            
            # Skip phones with "no daily task made" - don't report them as errors
            if phone_data['no_task_made']:
                continue
            
            # Check for accounts that didn't meet daily max (only for completed tasks)
            if phone_data['completed']:
                for account in phone_data['accounts']:
                    # Skip offline accounts
                    if account['status'] == 'off':
                        continue
                    
                    # Performance issue: didn't meet daily max (regardless of follow count)
                    if account['daily_max'] is not None and not account['met_max']:
                        self.failed_accounts.append({
                            'phone': phone_num,
                            'account': account['name'],
                            'error_type': 'Performance',
                            'reason': f"Failed to meet daily max of {account['daily_max']}"
                        })
    
    def get_success_rate(self) -> float:
        """Calculate success rate based on active phones and failed tasks."""
        if self.active_phones == 0:
            return 0.0
        successful = self.active_phones - len([f for f in self.failed_accounts if f['phone'] <= 20 or f['phone'] > 25])
        return (successful / self.active_phones * 100) if self.active_phones > 0 else 0.0
    
    def generate_summary(self) -> str:
        """
        Generate Discord-formatted summary report.
        """
        summary = []
        summary.append("📊 *INSTAGRAM AUTOMATION FARM ANALYSIS*")
        summary.append(f"📅 *Date:* December 31, 2025")
        summary.append("")
        
        # Device Activity Summary
        summary.append("═══════════════════════════════════════")
        summary.append("📈 *DEVICE ACTIVITY SUMMARY*")
        summary.append("═══════════════════════════════════════")
        summary.append(f"✅ *Total Follows Made:* {self.total_follows:,}")
        summary.append(f"❌ *Total Unfollows Made:* {self.total_unfollows:,}")
        summary.append(f"📱 *Active Devices:* {self.active_phones}/60")
        summary.append(f"📊 *Success Rate:* {self.get_success_rate():.1f}%")
        summary.append("")
        
        # Issues & Status Reasons
        if self.failed_accounts:
            summary.append("═══════════════════════════════════════")
            summary.append("⚠️ *FAILED ACCOUNTS & ERROR ANALYSIS*")
            summary.append("═══════════════════════════════════════")
            
            # Group by error type
            errors_by_type = defaultdict(list)
            for error in self.failed_accounts:
                errors_by_type[error['error_type']].append(error)
            
            error_num = 1
            for error_type in ['Configuration', 'Targeting', 'System', 'Performance']:
                if error_type in errors_by_type:
                    summary.append(f"\n*{error_type} Errors:*")
                    for error in errors_by_type[error_type]:
                        summary.append(f"  {error_num}. *Phone {error['phone']}* ({error['account']})")
                        summary.append(f"     └─ {error['reason']}")
                        error_num += 1
        else:
            summary.append("✅ *No critical errors detected!*")
        
        summary.append("")
        summary.append("═══════════════════════════════════════")
        
        return "\n".join(summary)
    
    def generate_custom_format(self, date_str: str = None) -> str:
        """
        Generate custom format report for chat display.
        
        Args:
            date_str: Optional date string to use in the report (e.g., "December 31, 2025")
        """
        report = []
        
        # Device Activity Summary
        if date_str:
            report.append(f"**📊 Device Activity Summary: {date_str}**")
        else:
            # Fallback to current date if not provided
            from datetime import datetime
            report.append(f"**📊 Device Activity Summary: {datetime.now().strftime('%B %d, %Y')}**")
        
        report.append(f"Total Follows Executed: **{self.total_follows:,}**")
        report.append(f"Total Unfollows Executed: **{self.total_unfollows:,}**")
        
        # Count active phones by method
        method_1_count = 0
        method_9_count = 0
        active_phone_nums = []
        for phone_data in self.phones.values():
            if phone_data['completed']:
                if phone_data['method'] == 1:
                    method_1_count += 1
                elif phone_data['method'] == 9:
                    method_9_count += 1
                active_phone_nums.append(phone_data['number'])
        
        report.append(f"Active Phones: **{self.active_phones}/60** ({method_1_count} Using Method 1, {method_9_count} Using Method 9)")
        
        # Get inactive phones
        inactive_phones = [p for p in range(1, 61) if p not in active_phone_nums]
        if inactive_phones:
            inactive_str = self._format_phone_ranges(inactive_phones)
            report.append(f"Inactive Phones: {inactive_str}")
        
        report.append("")
        
        # Performance Stats
        report.append("**:bar_chart: Automation Performance Stats**")
        report.append("**Scope:** Active Phones")
        
        successful_phones = self.active_phones - len(set(f['phone'] for f in self.failed_accounts if 'phone' in f))
        percent_met = (successful_phones / self.active_phones * 100) if self.active_phones > 0 else 0
        percent_issues = (len(set(f['phone'] for f in self.failed_accounts)) / self.active_phones * 100) if self.active_phones > 0 else 0
        
        report.append(f"• **% of Phones Met Daily Max:** {percent_met:.1f}% ({successful_phones}/{self.active_phones} phones)")
        report.append(f"• **% of Phones with Issues:** {percent_issues:.1f}% ({len(set(f['phone'] for f in self.failed_accounts))}/{self.active_phones} phones)")
        
        # Failed phone numbers
        failed_phones = sorted(set(f['phone'] for f in self.failed_accounts))
        if failed_phones:
            phones_str = ", ".join(str(p) for p in failed_phones)
            report.append(f"  (Phones {phones_str})")
        report.append("")
        
        # Issue & Status Reasons
        report.append("**:memo: Issue & Status Reasons**")
        
        if self.failed_accounts:
            errors_by_phone = defaultdict(list)
            for error in self.failed_accounts:
                errors_by_phone[error['phone']].append(error)
            
            for phone_num in sorted(errors_by_phone.keys()):
                for error in errors_by_phone[phone_num]:
                    report.append(f"**Phone {phone_num}** ({error['account']})")
                    report.append(f"  └─ **{error['error_type']} Error:** {error['reason']}")
        else:
            report.append("✅ **No issues detected!**")
        
        return "\n".join(report)
    
    def _format_phone_ranges(self, phones: List[int]) -> str:
        """
        Format phone numbers into ranges (e.g., 1-5, 10, 15-20, 25)
        """
        if not phones:
            return ""
        
        phones = sorted(set(phones))
        ranges = []
        start = phones[0]
        end = phones[0]
        
        for phone in phones[1:]:
            if phone == end + 1:
                end = phone
            else:
                if start == end:
                    ranges.append(str(start))
                else:
                    ranges.append(f"{start}-{end}")
                start = phone
                end = phone
        
        # Add the last range
        if start == end:
            ranges.append(str(start))
        else:
            ranges.append(f"{start}-{end}")
        
        return ", ".join(ranges)
    
    def generate_detailed_breakdown(self) -> str:
        """
        Generate line-by-line calculation breakdown for verification.
        """
        breakdown = []
        breakdown.append("📋 *LINE-BY-LINE CALCULATION BREAKDOWN*")
        breakdown.append("═══════════════════════════════════════\n")
        
        running_total = 0
        account_count = 0
        
        for phone_num in sorted(self.phones.keys()):
            phone_data = self.phones[phone_num]
            
            if not phone_data['completed']:
                continue
            
            breakdown.append(f"*Phone {phone_num}* (Method {phone_data['method']}):")
            
            for account in phone_data['accounts']:
                if account['status'] == 'off':
                    continue
                
                account_count += 1
                running_total += account['follows']
                
                status_indicator = "✅" if account['met_max'] else "❌"
                breakdown.append(
                    f"  {account_count}. {account['name']}: {account['follows']:>3} follows "
                    f"(max: {account['daily_max']}) {status_indicator} | Running Total: {running_total:,}"
                )
            
            breakdown.append("")
        
        breakdown.append(f"\n*FINAL TOTAL: {running_total:,} follows*")
        
        return "\n".join(breakdown)
    
    def set_excluded_phones(self, phone_range: List[int]) -> None:
        """
        Set phones to exclude from error reports (e.g., [21, 22, 23, 24, 25]).
        """
        self.excluded_phones = set(phone_range)
    
    def generate_full_report(self) -> str:
        """Generate complete report with summary and breakdown."""
        self.calculate_totals()
        self.identify_errors()
        
        report = []
        report.append(self.generate_summary())
        report.append("\n")
        report.append(self.generate_detailed_breakdown())
        
        return "\n".join(report)


def analyze_from_file(data_file: str = "data.txt", date_str: str = None) -> str:
    """
    Analyze data from file and generate client-ready report.
    This is the original working method that reads from data.txt.
    
    Args:
        data_file: Path to the data file (default: "data.txt")
        date_str: Optional date string to use in the report (e.g., "December 31, 2025")
        
    Returns:
        Formatted analysis report string, or empty string if error
    """
    try:
        # Read from file
        with open(data_file, 'r', encoding='utf-8') as f:
            raw_text = f.read()
        
        # Initialize analyzer
        analyzer = InstagramAutomationAnalyzer()
        
        # Optional: Set excluded phones if needed
        # analyzer.set_excluded_phones([21, 22, 23, 24, 25])
        
        # Parse the log data
        analyzer.parse_log(raw_text)
        
        # Generate and return the custom formatted report
        analyzer.calculate_totals()
        analyzer.identify_errors()
        
        report = analyzer.generate_custom_format(date_str=date_str)
        return report
        
    except Exception as e:
        # Return empty string on error - summary bot will still send its output
        import logging
        logger = logging.getLogger("instagram_analyzer")
        logger.exception("Error reading from file %s: %s", data_file, e)
        return ""