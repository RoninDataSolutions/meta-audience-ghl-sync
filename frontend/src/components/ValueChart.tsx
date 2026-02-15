import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
} from "chart.js";
import { Bar } from "react-chartjs-2";

ChartJS.register(CategoryScale, LinearScale, BarElement, Title, Tooltip);

interface Props {
  distribution: number[] | null;
}

const LABELS = [
  "0-10",
  "10-20",
  "20-30",
  "30-40",
  "40-50",
  "50-60",
  "60-70",
  "70-80",
  "80-90",
  "90-100",
];

export default function ValueChart({ distribution }: Props) {
  if (!distribution || distribution.length === 0) {
    return (
      <div className="card">
        <h2>Value Distribution</h2>
        <p className="muted">No data available. Run a sync first.</p>
      </div>
    );
  }

  const data = {
    labels: LABELS,
    datasets: [
      {
        label: "Contacts",
        data: distribution,
        backgroundColor: "rgba(59, 130, 246, 0.8)",
        borderRadius: 4,
      },
    ],
  };

  const options = {
    responsive: true,
    plugins: {
      title: { display: false },
    },
    scales: {
      y: {
        beginAtZero: true,
        ticks: { color: "#94a3b8" },
        grid: { color: "rgba(148, 163, 184, 0.1)" },
      },
      x: {
        ticks: { color: "#94a3b8" },
        grid: { display: false },
      },
    },
  };

  return (
    <div className="card">
      <h2>Value Distribution</h2>
      <Bar data={data} options={options} />
    </div>
  );
}
